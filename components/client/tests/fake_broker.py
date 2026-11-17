"""A fake broker for the tests of the client library: the broker API of DESIGN.md, section 6.1.1.

It implements only what the tests need. It runs on 127.0.0.1 with TLS. The certificate comes
from ``certtool.make_key_pair`` and stays in a temporary folder of pytest. The private key is
a test key, not a secret.
"""
from __future__ import annotations

import asyncio
import base64
import socket
import ssl
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from aiohttp import web
from aiohttp.typedefs import Handler

from agent_broker_client import certtool, messages, sse
from agent_broker_client.jsontypes import JsonObject, JsonValue

USERNAME = "broker-user"
PASSWORD = "broker-test-password"
CREATED_AT = "2026-11-16T10:00:00Z"
CLOSE = b""   # an item of a stream queue: close the stream


@dataclass
class TlsFiles:
    cert: Path
    key: Path


def make_tls_files(folder: Path, names: list[str] | None = None) -> TlsFiles:
    """Make a key pair with certtool in ``folder``."""
    cert_pem, key_pem = certtool.make_key_pair(names or ["localhost", "127.0.0.1"], 2)
    folder.mkdir(parents=True, exist_ok=True)
    files = TlsFiles(folder / "server.crt", folder / "server.key")
    files.cert.write_bytes(cert_pem)
    files.key.write_text(key_pem, encoding="ascii")
    return files


def session_body(agent: str, session_id: str, client: str | None, workspace: str, state: str = "idle") -> JsonObject:
    return messages.validate("Session", {
        "id": session_id, "agent": agent, "client": client, "workspace": workspace,
        "workspace_path": f"/work/{workspace}", "state": state, "title": None,
        "created_at": CREATED_AT, "last_modified": CREATED_AT})


def error(status: int, code: str, text: str) -> web.Response:
    return web.json_response(messages.error_body(code, text), status=status)


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str]
    body: JsonValue
    authorization: str | None


@dataclass
class FakeBroker:
    """The state of the fake broker. The tests change it and read it."""
    tls: TlsFiles
    agents: dict[str, str] = field(default_factory=lambda: {"claude": "running", "opencode": "exited"})
    config: dict[str, dict[str, JsonValue]] = field(default_factory=lambda: {
        "claude": {"claude.model": "opus", "log.level": "INFO"}})
    sessions: dict[str, JsonObject] = field(default_factory=dict)
    workspaces: set[str] = field(default_factory=lambda: {"x", "."})
    requests: list[Request] = field(default_factory=list)
    # A forced error answer for the next request that is not a stream: (status, error value).
    fail_next: tuple[int, str] | None = None
    # A forced raw answer for the next request that is not a stream: (status, body).
    raw_next: tuple[int, bytes] | None = None
    # A forced status for each connect of a session stream, e.g. 503.
    stream_status: int | None = None
    # With stream_status: send the header of the answer and no body.
    stream_hang: bool = False
    # A comment line on each stream after this time with no data. None: no heartbeat.
    heartbeat_interval: float | None = None
    # A wait before each answer that is not a stream, in seconds.
    delay: float = 0
    stream_connects: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    broker_connects: int = 0
    _streams: dict[str, list[asyncio.Queue[bytes]]] = field(default_factory=lambda: defaultdict(list))
    _broker_streams: list[asyncio.Queue[bytes]] = field(default_factory=list)
    _runner: web.AppRunner | None = None
    _next_id: int = 0
    port: int = 0

    @property
    def url(self) -> str:
        return f"https://127.0.0.1:{self.port}"

    async def start(self) -> None:
        app = web.Application(middlewares=[self._middleware])
        app.add_routes([
            web.get("/agents", self._agents),
            web.post("/agents/{agent}/{action:start|stop|restart}", self._control),
            web.get("/agents/{agent}/config", self._config_all),
            web.get("/agents/{agent}/config/{key}", self._config_get),
            web.put("/agents/{agent}/config/{key}", self._config_set),
            web.get("/events", self._broker_events),
            web.post("/agents/{agent}/sessions", self._create),
            web.get("/agents/{agent}/sessions", self._list),
            web.get("/agents/{agent}/sessions/{id}", self._get),
            web.delete("/agents/{agent}/sessions/{id}", self._delete),
            web.get("/agents/{agent}/sessions/{id}/events", self._session_events),
            web.post("/agents/{agent}/sessions/{id}/commands", self._command),
            web.post("/agents/{agent}/sessions/{id}/interrupt", self._interrupt),
        ])
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.tls.cert), str(self.tls.key))
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        self._runner = web.AppRunner(app, handler_cancellation=True, shutdown_timeout=0.5)
        await self._runner.setup()
        await web.SockSite(self._runner, sock, ssl_context=context).start()

    async def stop(self) -> None:
        self.close_all_streams()
        if self._runner is not None:
            await self._runner.cleanup()

    # --- Control of the streams by a test ------------------------------------------

    def add_session(self, agent: str, client: str | None = "client-1", workspace: str = "x",
                    state: str = "idle") -> str:
        self._next_id += 1
        session_id = f"ses_{self._next_id}"
        self.sessions[session_id] = session_body(agent, session_id, client, workspace, state)
        return session_id

    def open_streams(self, session_id: str) -> int:
        return len(self._streams[session_id])

    def push(self, session_id: str, event: str, body: JsonObject) -> None:
        """Send an event to each open stream of the session. Check the event first."""
        self.push_raw(session_id, sse.encode(event, messages.validate_event(event, body)))

    def push_raw(self, session_id: str, data: bytes) -> None:
        for queue in self._streams[session_id]:
            queue.put_nowait(data)

    def push_broker(self, event: str, body: JsonObject) -> None:
        for queue in self._broker_streams:
            queue.put_nowait(sse.encode(event, messages.validate_event(event, body)))

    def close_streams(self, session_id: str) -> None:
        for queue in self._streams[session_id]:
            queue.put_nowait(CLOSE)

    def close_all_streams(self) -> None:
        for queues in [*self._streams.values(), self._broker_streams]:
            for queue in queues:
                queue.put_nowait(CLOSE)

    # --- The API ------------------------------------------------------------------

    @web.middleware
    async def _middleware(self, request: web.Request,
                          handler: Handler) -> web.StreamResponse:
        text = await request.text()
        body: JsonValue = await request.json() if text else None
        authorization = request.headers.get("Authorization")
        self.requests.append(Request(request.method, request.path, dict(request.query), body, authorization))
        expected = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode("ascii")
        if authorization != expected:
            return error(401, "unauthorized", "the login is not correct")
        if self.fail_next is not None and not request.path.endswith("/events"):
            status, code = self.fail_next
            self.fail_next = None
            return error(status, code, "forced by the test")
        if self.raw_next is not None and not request.path.endswith("/events"):
            status, raw = self.raw_next
            self.raw_next = None
            return web.Response(status=status, body=raw, content_type="application/json")
        if self.delay and not request.path.endswith("/events"):
            await asyncio.sleep(self.delay)
        agent = request.match_info.get("agent")
        if agent is not None and agent not in self.agents:
            return error(404, "not_found", f"no agent {agent}")
        return await handler(request)

    async def _agents(self, request: web.Request) -> web.Response:
        agents: list[JsonValue] = [
            {"name": name, "provider": "opencode" if name == "opencode" else "agent_api", "container_state": state,
             "agent_state": "ok" if state == "running" else "unreachable", "version": "1.0"}
            for name, state in self.agents.items()]
        return web.json_response(messages.validate("AgentList", {"agents": agents}))

    async def _control(self, request: web.Request) -> web.Response:
        agent = request.match_info["agent"]
        action = request.match_info["action"]
        self.agents[agent] = "exited" if action == "stop" else "running"
        if action in ("stop", "restart"):
            for session_id, session in self.sessions.items():
                if session["agent"] == agent:
                    self.close_streams(session_id)
        return web.json_response(
            messages.validate("AgentResult", {"agent": agent, "container_state": self.agents[agent]}))

    async def _config_all(self, request: web.Request) -> web.Response:
        values: list[JsonValue] = [
            {"key": k, "value": v} for k, v in self.config.get(request.match_info["agent"], {}).items()]
        return web.json_response(messages.validate("ConfigValues", {"values": values}))

    async def _config_get(self, request: web.Request) -> web.Response:
        values = self.config.get(request.match_info["agent"], {})
        key = request.match_info["key"]
        if key not in values:
            return error(404, "not_found", f"no key {key}")
        return web.json_response(messages.validate("ConfigValue", {"key": key, "value": values[key]}))

    async def _config_set(self, request: web.Request) -> web.Response:
        body = messages.validate("ConfigSetRequest", await request.json())
        key = request.match_info["key"]
        self.config.setdefault(request.match_info["agent"], {})[key] = body["value"]
        return web.json_response(messages.validate(
            "ConfigSetResult", {"key": key, "value": body["value"], "applied": "at_restart"}))

    async def _create(self, request: web.Request) -> web.Response:
        try:
            body = messages.validate("SessionCreateRequest", await request.json())
        except messages.MessageError as exc:
            return error(400, "bad_request", str(exc))
        workspace = str(body["workspace"]).strip("/") or "."
        if workspace not in self.workspaces and not body.get("force", False):
            return error(404, "workspace_not_found", f"no folder {workspace}")
        self.workspaces.add(workspace)
        session_id = self.add_session(request.match_info["agent"], str(body["client"]), workspace)
        return web.json_response(self.sessions[session_id], status=201)

    async def _list(self, request: web.Request) -> web.Response:
        client = request.query.get("client")
        if not client:
            return error(400, "bad_request", "client is required")
        agent = request.match_info["agent"]
        sessions: list[JsonValue] = [
            s for s in self.sessions.values() if s["agent"] == agent and s["client"] == client]
        return web.json_response(messages.validate("SessionList", {"sessions": sessions}))

    def _session(self, request: web.Request) -> JsonObject | None:
        session = self.sessions.get(request.match_info["id"])
        if session is None or session["agent"] != request.match_info["agent"]:
            return None
        return session

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
        if session["state"] == "working":
            return error(409, "busy", "a command runs")
        session["state"] = "working"
        self.push(str(session["id"]), "state", {"agent": session["agent"], "session": session["id"], "state": "working"})
        return web.json_response(messages.validate("CommandAccepted", {"id": body["id"], "status": "accepted"}),
                                 status=202)

    async def _interrupt(self, request: web.Request) -> web.Response:
        session = self._session(request)
        if session is None:
            return error(404, "not_found", "no session")
        session["state"] = "idle"
        return web.json_response(messages.validate("SessionResult", {"session_state": "idle"}))

    async def _stream(self, request: web.Request, queues: list[asyncio.Queue[bytes]], first: bytes | None) -> web.StreamResponse:
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
        await response.prepare(request)
        queue: asyncio.Queue[bytes] = asyncio.Queue()
        queues.append(queue)
        try:
            if first is not None:
                await response.write(first)
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), self.heartbeat_interval)
                except TimeoutError:
                    await response.write(sse.COMMENT)
                    continue
                if item == CLOSE:
                    break
                await response.write(item)
        finally:
            queues.remove(queue)
        return response

    async def _session_events(self, request: web.Request) -> web.StreamResponse:
        session_id = request.match_info["id"]
        self.stream_connects[session_id] += 1
        session = self._session(request)
        if session is None:
            return error(404, "not_found", "no session")
        if self.stream_status is not None and self.stream_hang:
            hang = web.StreamResponse(status=self.stream_status, headers={"Content-Length": "100"})
            await hang.prepare(request)
            await asyncio.Event().wait()   # the test stops the broker; the server cancels this handler
        if self.stream_status is not None:
            return error(self.stream_status, "agent_unavailable", "forced by the test")
        state = sse.encode("state", {"agent": session["agent"], "session": session_id, "state": session["state"]})
        return await self._stream(request, self._streams[session_id], state)

    async def _broker_events(self, request: web.Request) -> web.StreamResponse:
        self.broker_connects += 1
        return await self._stream(request, self._broker_streams, None)
