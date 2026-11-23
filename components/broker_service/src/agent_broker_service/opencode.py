"""The provider ``opencode``: the broker API on the HTTP API of OpenCode 2.2.7.

The provider keeps no state on disk. The session settings stay in the session at OpenCode: the model in its
model, the tool policy in its permission rules and its metadata. The live events come from the event stream of
OpenCode (``/api/event``), filtered by the session.
"""
from __future__ import annotations

import base64
import codecs
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from urllib.parse import quote

import aiohttp
from agent_broker_client import messages
from agent_broker_client.jsontypes import JsonObject, JsonValue
from yarl import URL

from . import logs
from .errors import AgentError, AgentUnavailableError
from .provider import AgentProvider, Event, UpstreamStream

log = logs.get("opencode")

EFFORT = "auto"         # OpenCode has no effort for the broker
PAGE_LIMIT = 100
# OpenCode asks before it reads an env file. A broker session has no person to answer: deny it.
ENV_RULES: list[JsonValue] = [{"action": "read", "resource": "*.env", "effect": "deny"},
                              {"action": "read", "resource": "*.env.*", "effect": "deny"}]
type Detect = Callable[[str], str | None]
# Make sure that a workspace folder exists in the container of OpenCode: (path, create) -> it exists now.
type FolderCheck = Callable[[str, bool], Awaitable[bool]]


def message_id(command_id: str) -> str:
    """The message ID of OpenCode for a command ID of the broker (a UUID): ``msg_`` and the hex digits."""
    try:
        return "msg_" + uuid.UUID(command_id).hex
    except ValueError:
        raise AgentError(400, messages.error_body("bad_request", "the command ID is not a UUID")) from None


def command_id(message: str) -> str | None:
    """The command ID of the broker for a message ID of OpenCode, or None for a message of another tool."""
    if not message.startswith("msg_"):
        return None
    try:
        return str(uuid.UUID(hex=message[4:]))
    except ValueError:
        return None


def permission_rules(tools: JsonObject | None) -> list[JsonValue]:
    """The permission rules of a session for a ToolPolicy. ``allow`` null adds no rule; the deny rules come last."""
    rules: list[JsonValue] = []
    if tools is not None:
        allow, deny = tools.get("allow"), tools.get("deny")
        if isinstance(allow, list):
            rules.append({"action": "*", "resource": "*", "effect": "deny"})
            rules += [{"action": str(name), "resource": "*", "effect": "allow"} for name in allow]
        if isinstance(deny, list):
            rules += [{"action": str(name), "resource": "*", "effect": "deny"} for name in deny]
    return rules + ENV_RULES


def _iso(ms: JsonValue) -> str:
    seconds = float(ms) / 1000 if isinstance(ms, (int, float)) else 0.0
    return datetime.fromtimestamp(seconds, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _model_id(ref: JsonValue) -> str:
    if isinstance(ref, dict) and isinstance(ref.get("id"), str) and isinstance(ref.get("providerID"), str):
        return f"{ref['providerID']}/{ref['id']}"
    return ""


def _model_ref(model: str) -> JsonObject:
    provider, _, model_id = model.partition("/")
    return {"id": model_id, "providerID": provider}


def _data(answer: JsonValue, where: str) -> JsonObject:
    data = answer.get("data") if isinstance(answer, dict) else None
    if not isinstance(data, dict):
        raise AgentUnavailableError(f"{where}: the answer of OpenCode has no data")
    return data


def _state(info: JsonObject) -> str:
    """``working`` while the last change of the session is busy, else ``idle``."""
    times = info.get("time")
    if isinstance(times, dict):
        busy, idle = times.get("busy"), times.get("idle")
        if isinstance(busy, (int, float)) and (not isinstance(idle, (int, float)) or busy > idle):
            return "working"
    return "idle"


class OpencodeProvider(AgentProvider):
    def __init__(self, agent: str, url: str, password: str, request_timeout: float, workspace_root: str,
                 detect: Detect, folders: FolderCheck, reply_replay: float = 600.0):
        self._agent = agent
        self._url = URL(url.rstrip("/"))
        self._auth = "Basic " + base64.b64encode(f"opencode:{password}".encode()).decode()
        self._timeout = request_timeout
        self._root = workspace_root.rstrip("/") or "/"
        self._detect = detect
        self._folders = folders
        self._reply_replay = reply_replay
        self._session: aiohttp.ClientSession | None = None
        self._started = time.monotonic()

    @property
    def agent(self) -> str:
        return self._agent

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={"Authorization": self._auth})
        return self._session

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()

    async def _call(self, method: str, path: str, body: JsonObject | None = None,
                    params: dict[str, str] | None = None) -> JsonValue:
        try:
            async with self._http().request(method, self._url.with_path(path), json=body, params=params,
                                            timeout=aiohttp.ClientTimeout(total=self._timeout)) as answer:
                raw = await answer.read()
                status = answer.status
        except (aiohttp.ClientError, OSError, TimeoutError) as exc:
            raise AgentUnavailableError(f"{self._agent}: {method} {path}: OpenCode is not reachable: {exc}") from None
        text = raw[:300].decode("utf-8", "replace")
        if status == 404:
            raise AgentError(404, messages.error_body("not_found", "no such session"))
        if status == 409:
            raise AgentError(409, messages.error_body("busy", text))
        if status == 400:
            raise AgentError(400, messages.error_body("bad_request", text))
        if status >= 300:
            raise AgentUnavailableError(f"{self._agent}: {method} {path}: OpenCode answered {status}: {text}")
        try:
            value: JsonValue = json.loads(raw) if raw else None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise AgentUnavailableError(f"{self._agent}: {method} {path}: the answer is not JSON") from None
        return value

    # --- status and configuration ------------------------------------------------------

    async def status(self) -> JsonObject:
        sessions = await self.list_sessions()
        open_sessions = sum(1 for s in sessions if s.get("state") == "working")
        return {"uptime_s": int(time.monotonic() - self._started), "version": "", "open_sessions": open_sessions}

    async def get_config(self) -> JsonObject:
        return {"values": []}

    async def get_config_value(self, key: str) -> JsonObject:
        raise AgentError(404, messages.error_body("not_found", "the agent opencode has no configuration key"))

    async def set_config(self, key: str, value: JsonValue) -> JsonObject:
        raise AgentError(404, messages.error_body("not_found", "the agent opencode has no configuration key"))

    # --- settings ------------------------------------------------------------------------

    async def _models(self) -> tuple[list[JsonValue], str]:
        listed = await self._call("GET", "/api/model")
        items = listed.get("data") if isinstance(listed, dict) else None
        models: list[JsonValue] = []
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict):
                model = _model_id(item)
                name = item.get("name")
                if model:
                    models.append({"id": model, "name": name if isinstance(name, str) else model})
        default = await self._call("GET", "/api/model/default")
        chosen = _model_id(default.get("data") if isinstance(default, dict) else None)
        ids = [m["id"] for m in models if isinstance(m, dict)]
        if chosen not in ids:
            chosen = str(ids[0]) if ids else ""
        return models, chosen

    async def list_models(self) -> JsonObject:
        models, default = await self._models()
        if not default:
            raise AgentUnavailableError(f"{self._agent}: OpenCode has no model")
        return {"models": models, "default": default}

    async def list_efforts(self) -> JsonObject:
        return {"efforts": [EFFORT], "default": EFFORT}

    async def _check(self, model: str | None, effort: str | None) -> None:
        if effort is not None and effort != EFFORT:
            raise AgentError(400, messages.error_body("invalid_setting", f"the agent opencode has only the effort {EFFORT}"))
        if model is not None:
            models, _ = await self._models()
            if model not in [m.get("id") for m in models if isinstance(m, dict)]:
                raise AgentError(400, messages.error_body("invalid_setting", f"the model {model} is not in GET /models"))

    # --- sessions ------------------------------------------------------------------------

    def _workspace(self, workspace: str) -> tuple[str, str]:
        """The relative form and the folder of a workspace. Only the root or one folder directly below it."""
        name = workspace.strip().removeprefix("./").strip("/") or "."
        if name == ".":
            return ".", self._root
        if "/" in name or name in ("..",) or name.startswith("."):
            raise AgentError(400, messages.error_body("bad_request", "the workspace must be the root or one folder below it"))
        return name, f"{self._root}/{name}"

    def _session_body(self, info: JsonObject) -> JsonObject:
        metadata = info.get("metadata") if isinstance(info.get("metadata"), dict) else {}
        meta = metadata if isinstance(metadata, dict) else {}
        location = info.get("location")
        path = location.get("directory") if isinstance(location, dict) else None
        workspace = meta.get("workspace")
        tools = meta.get("tools")
        times = info.get("time") if isinstance(info.get("time"), dict) else {}
        when = times if isinstance(times, dict) else {}
        title = info.get("title")
        client = meta.get("client")
        body: JsonObject = {
            "id": str(info.get("id")), "agent": self._agent,
            "client": client if isinstance(client, str) else None,
            "workspace": workspace if isinstance(workspace, str) else ".",
            "workspace_path": path if isinstance(path, str) else self._root,
            "state": _state(info), "title": title if isinstance(title, str) and title else None,
            "created_at": _iso(when.get("created")), "last_modified": _iso(when.get("updated") or when.get("created")),
            "model": _model_id(info.get("model")) or "unknown", "effort": EFFORT,
            "tools": tools if isinstance(tools, dict) else {"allow": None, "deny": []},
        }
        return messages.validate("Session", body)

    async def create_session(self, request: JsonObject) -> JsonObject:
        model = str(request["model"])
        effort = request.get("effort")
        await self._check(model, str(effort) if effort is not None else None)
        name, path = self._workspace(str(request["workspace"]))
        # OpenCode accepts a session in a folder that does not exist, but its prompts then fail.
        if name != "." and not await self._folders(path, request.get("force") is True):
            raise AgentError(404, messages.error_body("workspace_not_found", f"no folder {name}"))
        raw_tools = request.get("tools")
        tools = raw_tools if isinstance(raw_tools, dict) else {"allow": None, "deny": []}
        body: JsonObject = {
            "location": {"directory": path}, "model": _model_ref(model), "permissions": permission_rules(tools),
            "metadata": {"client": request["client"], "workspace": name, "tools": tools},
        }
        created = _data(await self._call("POST", "/api/session", body), f"{self._agent}: create session")
        log.info("Session %s created in %s with %s", created.get("id"), path, model)
        return self._session_body(created)

    async def list_sessions(self) -> list[JsonObject]:
        sessions: list[JsonObject] = []
        params = {"limit": str(PAGE_LIMIT)}
        for _ in range(100):
            answer = await self._call("GET", "/api/session", params=params)
            items = answer.get("data") if isinstance(answer, dict) else None
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and not item.get("parentID"):
                    sessions.append(self._session_body(item))
            cursor = answer.get("cursor") if isinstance(answer, dict) else None
            following = cursor.get("next") if isinstance(cursor, dict) else None
            if not isinstance(following, str) or not following:
                break
            params = {"limit": str(PAGE_LIMIT), "cursor": following}
        return sessions

    async def _info(self, session_id: str) -> JsonObject:
        return _data(await self._call("GET", f"/api/session/{quote(session_id, safe='')}"), f"{self._agent}: session")

    async def get_session(self, session_id: str) -> JsonObject:
        return self._session_body(await self._info(session_id))

    async def delete_session(self, session_id: str) -> None:
        await self._call("DELETE", f"/api/session/{quote(session_id, safe='')}")

    async def set_session_settings(self, session_id: str, request: JsonObject) -> JsonObject:
        model = request.get("model")
        effort = request.get("effort")
        await self._check(str(model) if model is not None else None, str(effort) if effort is not None else None)
        info = await self._info(session_id)
        if _state(info) == "working":
            raise AgentError(409, messages.error_body("busy", "a command runs"))
        if model is not None:
            await self._call("POST", f"/api/session/{quote(session_id, safe='')}/model", {"model": _model_ref(str(model))})
        return await self.get_session(session_id)

    async def send_command(self, session_id: str, request: JsonObject) -> JsonObject:
        command = str(request["id"])
        info = await self._info(session_id)
        if _state(info) == "working":
            raise AgentError(409, messages.error_body("busy", "a command runs"))
        try:
            await self._call("POST", f"/api/session/{quote(session_id, safe='')}/prompt",
                             {"id": message_id(command), "text": str(request["text"])})
        except AgentError as exc:
            if exc.status == 409:       # the same message ID: the command exists
                return {"id": command, "status": "duplicate"}
            raise
        return {"id": command, "status": "accepted"}

    async def interrupt(self, session_id: str) -> JsonObject:
        await self._call("POST", f"/api/session/{quote(session_id, safe='')}/interrupt")
        return {"session_state": _state(await self._info(session_id))}

    # --- streams -------------------------------------------------------------------------

    async def _last_reply(self, session_id: str) -> JsonObject | None:
        """The last reply of the session, if it is not older than REPLY_REPLAY, for the start of a stream."""
        context = await self._call("GET", f"/api/session/{quote(session_id, safe='')}/context")
        items = context.get("data") if isinstance(context, dict) else None
        if not isinstance(items, list):
            return None
        user_id: str | None = None
        reply: JsonObject | None = None
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "user" and isinstance(item.get("id"), str):
                user_id = str(item["id"])
            elif item.get("type") == "assistant":
                times = item.get("time") if isinstance(item.get("time"), dict) else {}
                done = times.get("completed") if isinstance(times, dict) else None
                content = item.get("content")
                text = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict)
                               and part.get("type") == "text") if isinstance(content, list) else ""
                command = command_id(user_id) if user_id else None
                if command and isinstance(done, (int, float)) and time.time() - done / 1000 <= self._reply_replay:
                    reply = {"session": session_id, "command_id": command, "message": text,
                             "language": self._detect(text) or "en"}
        return reply

    async def open_session_stream(self, session_id: str) -> UpstreamStream:
        info = await self._info(session_id)
        first: list[Event] = [("state", {"session": session_id, "state": _state(info)})]
        replay = await self._last_reply(session_id)
        if replay is not None:
            first.append(("reply", replay))
        response = await self._open_events()
        return OpencodeStream(response, session_id, first, self._detect, f"{self._agent} session {session_id}")

    async def open_agent_stream(self) -> UpstreamStream:
        return OpencodeStream(await self._open_events(), None, [], self._detect, f"{self._agent} agent stream")

    async def _open_events(self) -> aiohttp.ClientResponse:
        try:
            response = await self._http().get(self._url.with_path("/api/event"),
                                              headers={"Accept": "text/event-stream"},
                                              timeout=aiohttp.ClientTimeout(total=None, sock_connect=self._timeout))
        except (aiohttp.ClientError, OSError, TimeoutError) as exc:
            raise AgentUnavailableError(f"{self._agent}: the event stream of OpenCode is not reachable: {exc}") from None
        if response.status != 200:
            response.close()
            raise AgentUnavailableError(f"{self._agent}: the event stream of OpenCode answered {response.status}")
        return response


class OpencodeStream(UpstreamStream):
    """The events of one session from the event stream of OpenCode, as events of the agent API.

    With no session (the agent stream), the stream gives no event: OpenCode has no lifecycle or log events for it.
    """

    def __init__(self, response: aiohttp.ClientResponse, session_id: str | None, first: list[Event],
                 detect: Detect, label: str):
        self._response = response
        self._session = session_id
        self._first = first
        self._detect = detect
        self.label = label
        self._command: str | None = None    # the command of the running turn
        self._text = ""                     # the last text of the running turn

    def _map(self, item: JsonObject) -> list[Event]:
        kind = item.get("type")
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        payload = data if isinstance(data, dict) else {}
        if self._session is None or payload.get("sessionID") != self._session:
            return []
        sid = self._session
        if kind == "session.inbox.delivered":
            inbox = payload.get("inboxID")
            self._command = command_id(inbox) if isinstance(inbox, str) else None
            self._text = ""
            return []
        if kind == "session.execution.started":
            return [("state", {"session": sid, "state": "working"})]
        if kind == "session.tool.called":
            return [("status", {"session": sid, "kind": "activity", "text": "using a tool"})]
        if kind == "session.step.started":
            return [("status", {"session": sid, "kind": "activity", "text": "thinking"})]
        if kind == "session.text.ended":
            text = payload.get("text")
            self._text = text if isinstance(text, str) else self._text
            return []
        command = self._command
        if kind == "session.execution.succeeded":
            events: list[Event] = []
            if command is not None:
                events.append(("reply", {"session": sid, "command_id": command, "message": self._text,
                                         "language": self._detect(self._text) or "en"}))
            return [*events, ("state", {"session": sid, "state": "idle"})]
        if kind in ("session.execution.failed", "session.execution.interrupted"):
            failed = kind == "session.execution.failed"
            error = payload.get("error")
            text = json.dumps(error)[:300] if failed else f"interrupted: {payload.get('reason')}"
            events = []
            if command is not None:
                events.append(("notice", {"session": sid, "kind": "command_failed" if failed else "command_dropped",
                                          "command_id": command, "text": text}))
            return [*events, ("state", {"session": sid, "state": "idle"})]
        return []

    async def events(self) -> AsyncIterator[Event]:
        for first in self._first:
            yield first
        text = codecs.getincrementaldecoder("utf-8")(errors="replace")
        buffer = ""
        try:
            async for chunk in self._response.content.iter_any():
                buffer += text.decode(chunk)
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if not line.startswith("data:"):
                        continue
                    try:
                        item: JsonValue = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(item, dict):
                        continue
                    for name, body in self._map(item):
                        try:
                            yield name, messages.validate_agent_event(name, body)
                        except messages.MessageError as exc:
                            log.warning("%s: an event is not correct; it is not passed: %s", self.label, exc.summary)
        except (aiohttp.ClientError, OSError) as exc:
            raise AgentUnavailableError(f"{self.label}: the stream broke: {exc}") from None

    async def close(self) -> None:
        self._response.close()
