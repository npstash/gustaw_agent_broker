"""A fake agent for the tests of the broker: the agent API.

It runs on 127.0.0.1 with HTTP, no TLS and no authentication. It implements only what the
tests need. The tests control its sessions, its events, its errors and the end of its streams.
"""
from __future__ import annotations

import asyncio
import socket
from collections import defaultdict
from dataclasses import dataclass, field

from aiohttp import web
from aiohttp.typedefs import Handler

from agent_broker_client import messages, sse
from agent_broker_client.jsontypes import JsonObject, JsonValue

CREATED_AT = "2026-11-16T10:00:00Z"
# The agent does not know its name in the broker. It writes this name; the broker replaces it.
OWN_NAME = "local-agent"
CLOSE = b""   # an item of a stream queue: close the stream


def workspace_of(path: str) -> str | None:
    """The relative form of a workspace path. None for a path outside the root."""
    parts: list[str] = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    if len(parts) > 1:
        return None     # a nested path
    return "/".join(parts) or "."


def error(status: int, code: str, text: str) -> web.Response:
    return web.json_response(messages.error_body(code, text), status=status)


MODELS = ("opus", "sonnet")
EFFORTS = ("low", "medium", "high")


@dataclass
class FakeAgent:
    """The state of the fake agent. The tests change it and read it."""
    version: str = "2.0"
    sessions: dict[str, JsonObject] = field(default_factory=dict)
    workspaces: set[str] = field(default_factory=lambda: {".", "x"})
    config: dict[str, JsonValue] = field(default_factory=lambda: {"claude.model": "opus", "log.level": "INFO"})
    last_command: dict[str, str] = field(default_factory=dict)
    requests: list[tuple[str, str]] = field(default_factory=list)
    # A forced raw answer for the next request that is not a stream: (status, body).
    raw_next: tuple[int, bytes] | None = None
    # The headers of the forced raw answer, e.g. ``Location``.
    raw_next_headers: dict[str, str] = field(default_factory=dict)
    session_connects: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    session_ends: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    agent_connects: int = 0
    port: int = 0
    _streams: dict[str, list[asyncio.Queue[bytes]]] = field(default_factory=lambda: defaultdict(list))
    _agent_streams: list[asyncio.Queue[bytes]] = field(default_factory=list)
    _runner: web.AppRunner | None = None
    _next_id: int = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def running(self) -> bool:
        return self._runner is not None

    async def start(self) -> None:
        """Start the server. A second start uses the same port, so the broker finds the agent again."""
        app = web.Application(middlewares=[self._middleware])
        app.add_routes([
            web.get("/status", self._status),
            web.get("/config", self._config_all),
            web.get("/config/{key}", self._config_get),
            web.put("/config/{key}", self._config_set),
            web.get("/events", self._agent_events),
            web.get("/models", self._models),
            web.get("/efforts", self._efforts),
            web.put("/sessions/{id}/settings", self._settings),
            web.post("/sessions", self._create),
            web.get("/sessions", self._list),
            web.get("/sessions/{id}", self._get),
            web.delete("/sessions/{id}", self._delete),
            web.get("/sessions/{id}/events", self._session_events),
            web.post("/sessions/{id}/commands", self._command),
            web.post("/sessions/{id}/interrupt", self._interrupt),
        ])
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", self.port))
        self.port = sock.getsockname()[1]
        self._runner = web.AppRunner(app, handler_cancellation=True, shutdown_timeout=0.5)
        await self._runner.setup()
        await web.SockSite(self._runner, sock).start()

    async def stop(self) -> None:
        self.close_all_streams()
        runner, self._runner = self._runner, None
        if runner is not None:
            await runner.cleanup()

    # --- Control by a test -------------------------------------------------------------

    def add_session(self, client: str | None = "client-1", workspace: str = "x", state: str = "idle",
                    model: str = "opus", effort: str = "medium", tools: JsonObject | None = None) -> str:
        self._next_id += 1
        session_id = f"ses_{self._next_id}"
        self.sessions[session_id] = {
            "id": session_id, "agent": OWN_NAME, "client": client, "workspace": workspace,
            "workspace_path": f"/work/{workspace}", "state": state, "title": None,
            "created_at": CREATED_AT, "last_modified": CREATED_AT, "model": model, "effort": effort,
            "tools": tools if tools is not None else {"allow": None, "deny": []}}
        return session_id

    def open_streams(self, session_id: str) -> int:
        return len(self._streams[session_id])

    @property
    def open_agent_streams(self) -> int:
        return len(self._agent_streams)

    def push(self, session_id: str, event: str, body: JsonObject) -> None:
        """Send an event of the agent API to each open stream of the session. Check it first."""
        self.push_raw(session_id, sse.encode(event, messages.validate_agent_event(event, {"session": session_id, **body})))

    def push_raw(self, session_id: str, data: bytes) -> None:
        for queue in self._streams[session_id]:
            queue.put_nowait(data)

    def push_agent(self, event: str, body: JsonObject) -> None:
        for queue in self._agent_streams:
            queue.put_nowait(sse.encode(event, messages.validate_agent_event(event, body)))

    def set_state(self, session_id: str, state: str) -> None:
        self.sessions[session_id]["state"] = state
        self.push(session_id, "state", {"state": state})

    def close_streams(self, session_id: str) -> None:
        for queue in self._streams[session_id]:
            queue.put_nowait(CLOSE)

    def close_all_streams(self) -> None:
        for queues in [*self._streams.values(), self._agent_streams]:
            for queue in queues:
                queue.put_nowait(CLOSE)

    # --- The agent API -----------------------------------------------------------------

    @web.middleware
    async def _middleware(self, request: web.Request, handler: Handler) -> web.StreamResponse:
        self.requests.append((request.method, request.path_qs))
        if self.raw_next is not None and not request.path.endswith("/events"):
            status, raw = self.raw_next
            self.raw_next = None
            headers, self.raw_next_headers = self.raw_next_headers, {}
            return web.Response(status=status, body=raw, content_type="application/json", headers=headers)
        return await handler(request)

    async def _status(self, request: web.Request) -> web.Response:
        open_sessions = sum(1 for queues in self._streams.values() if queues)
        return web.json_response(messages.validate(
            "AgentStatus", {"uptime_s": 1, "version": self.version, "open_sessions": open_sessions}))

    async def _config_all(self, request: web.Request) -> web.Response:
        values: list[JsonValue] = [{"key": k, "value": v} for k, v in self.config.items()]
        return web.json_response(messages.validate("ConfigValues", {"values": values}))

    async def _config_get(self, request: web.Request) -> web.Response:
        key = request.match_info["key"]
        if key not in self.config:
            return error(404, "not_found", f"no key {key}")
        return web.json_response(messages.validate("ConfigValue", {"key": key, "value": self.config[key]}))

    async def _config_set(self, request: web.Request) -> web.Response:
        key = request.match_info["key"]
        body = messages.validate("ConfigSetRequest", await request.json())
        if key not in self.config:
            return error(404, "not_found", f"no key {key}")
        self.config[key] = body["value"]
        return web.json_response(messages.validate(
            "ConfigSetResult", {"key": key, "value": body["value"], "applied": "now"}))

    async def _create(self, request: web.Request) -> web.Response:
        try:
            body = messages.validate("SessionCreateRequest", await request.json())
        except messages.MessageError as exc:
            return error(400, "bad_request", str(exc))
        workspace = workspace_of(str(body["workspace"]))
        if workspace is None:
            return error(400, "bad_request", "the workspace is outside the root")
        if workspace not in self.workspaces and not body.get("force", False):
            return error(404, "workspace_not_found", f"no folder {workspace}")
        effort = str(body.get("effort", "medium"))
        if body["model"] not in MODELS or effort not in EFFORTS:
            return error(400, "invalid_setting", "unknown model or effort")
        self.workspaces.add(workspace)
        tools = body.get("tools")
        session_id = self.add_session(str(body["client"]), workspace, model=str(body["model"]), effort=effort,
                                      tools=tools if isinstance(tools, dict) else None)
        return web.json_response(self.sessions[session_id], status=201)

    async def _models(self, request: web.Request) -> web.Response:
        return web.json_response({"models": [{"id": m, "name": m.title()} for m in MODELS], "default": "opus"})

    async def _efforts(self, request: web.Request) -> web.Response:
        return web.json_response({"efforts": list(EFFORTS), "default": "medium"})

    async def _settings(self, request: web.Request) -> web.Response:
        session = self._session(request)
        if session is None:
            return error(404, "not_found", "no session")
        try:
            body = messages.validate("SessionSettingsRequest", await request.json())
        except messages.MessageError as exc:
            return error(400, "bad_request", str(exc))
        if body.get("model", "opus") not in MODELS or body.get("effort", "medium") not in EFFORTS:
            return error(400, "invalid_setting", "unknown model or effort")
        if session["state"] == "working":
            return error(409, "busy", "a command runs")
        for key in ("model", "effort"):
            if key in body:
                session[key] = body[key]
        return web.json_response(session)

    async def _list(self, request: web.Request) -> web.Response:
        sessions: list[JsonValue] = list(self.sessions.values())
        return web.json_response({"sessions": sessions})

    def _session(self, request: web.Request) -> JsonObject | None:
        return self.sessions.get(request.match_info["id"])

    async def _get(self, request: web.Request) -> web.Response:
        session = self._session(request)
        if session is None:
            return error(404, "not_found", "no session")
        return web.json_response(session)

    async def _delete(self, request: web.Request) -> web.Response:
        session = self._session(request)
        if session is None:
            return error(404, "not_found", "no session")
        del self.sessions[request.match_info["id"]]
        self.close_streams(request.match_info["id"])
        return web.Response(status=204)

    async def _command(self, request: web.Request) -> web.Response:
        session = self._session(request)
        if session is None:
            return error(404, "not_found", "no session")
        body = messages.validate("CommandRequest", await request.json())
        session_id = str(session["id"])
        command_id = str(body["id"])
        if self.last_command.get(session_id) == command_id:
            return web.json_response({"id": command_id, "status": "duplicate"}, status=202)
        if session["state"] == "working":
            return error(409, "busy", "a command runs")
        self.last_command[session_id] = command_id
        self.set_state(session_id, "working")
        return web.json_response({"id": command_id, "status": "accepted"}, status=202)

    async def _interrupt(self, request: web.Request) -> web.Response:
        session = self._session(request)
        if session is None:
            return error(404, "not_found", "no session")
        self.set_state(str(session["id"]), "idle")
        return web.json_response({"session_state": "idle"})

    async def _stream(self, request: web.Request, queues: list[asyncio.Queue[bytes]],
                      first: bytes | None) -> web.StreamResponse:
        """Write the items of a queue. No heartbeat: an agent sends none."""
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await response.prepare(request)
        queue: asyncio.Queue[bytes] = asyncio.Queue()
        queues.append(queue)
        try:
            if first is not None:
                await response.write(first)
            while True:
                item = await queue.get()
                if item == CLOSE:
                    break
                await response.write(item)
        finally:
            queues.remove(queue)
        return response

    async def _session_events(self, request: web.Request) -> web.StreamResponse:
        session_id = request.match_info["id"]
        session = self._session(request)
        if session is None:
            return error(404, "not_found", "no session")
        self.session_connects[session_id] += 1
        state = sse.encode("state", {"session": session_id, "state": session["state"]})
        try:
            return await self._stream(request, self._streams[session_id], state)
        finally:
            self.session_ends[session_id] += 1

    async def _agent_events(self, request: web.Request) -> web.StreamResponse:
        self.agent_connects += 1
        return await self._stream(request, self._agent_streams, None)
