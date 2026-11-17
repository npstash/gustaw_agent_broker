"""The HTTPS API of the broker (DESIGN.md, section 6.1.1).

Each request needs HTTP basic authentication. The broker checks each request body and
each answer against the schema. An error gives an ``ErrorResponse``.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import json
from collections.abc import Awaitable, Callable

from aiohttp import web
from aiohttp.typedefs import Handler

from agent_broker_client import messages
from agent_broker_client.jsontypes import JsonObject, JsonValue
from agent_broker_client.messages import MessageError
from agent_broker_client.models import AgentState, ContainerState

from . import logs
from .docker import DockerEngine
from .errors import (AgentError, AgentUnavailableError, BadRequestError, ContainerMissingError,
                     DockerUnavailableError, NotFoundError)
from .proxy import EventProxy
from .router import AgentEntry, Router

log = logs.get("api")


def json_answer(name: str, body: JsonObject, status: int = 200) -> web.Response:
    """An answer that agrees with the schema of the message ``name``."""
    messages.validate(name, body)
    return web.json_response(body, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False))


def error_answer(status: int, error: str, message: str) -> web.Response:
    response = json_answer("ErrorResponse", {"error": error, "message": message}, status)
    if status == 401:
        response.headers["WWW-Authenticate"] = 'Basic realm="agent-broker"'
    return response


def credentials_match(header: str | None, username: str, password: str) -> bool:
    """Check the HTTP basic authentication header. Compare in constant time."""
    if not header or not header.startswith("Basic "):
        return False
    try:
        given = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    given_user, sep, given_password = given.partition(":")
    ok_user = hmac.compare_digest(given_user.encode(), username.encode())
    ok_password = hmac.compare_digest(given_password.encode(), password.encode())
    return bool(sep) and ok_user and ok_password


async def _body(request: web.Request, name: str) -> JsonObject:
    """The request body, checked against the schema of ``name``. Raise BadRequestError."""
    try:
        body: JsonValue = json.loads(await request.text())
    except web.HTTPRequestEntityTooLarge:
        raise BadRequestError("the body is too large") from None
    except (json.JSONDecodeError, UnicodeDecodeError, LookupError, RecursionError):
        raise BadRequestError("the body is not JSON") from None
    try:
        messages.validate(name, body)
    except MessageError as exc:
        raise BadRequestError(str(exc)) from None
    if not isinstance(body, dict):
        raise BadRequestError("the body is not an object")
    return body


class Api:
    """The routes of the broker API. ``closing`` is true while the broker stops."""

    def __init__(self, router: Router, docker: DockerEngine, proxy: EventProxy, username: str, password: str):
        self.router = router
        self.docker = docker
        self.proxy = proxy
        self._username = username
        self._password = password
        self.closing = False

    def app(self) -> web.Application:
        app = web.Application(middlewares=[self._middleware])
        app.add_routes([
            web.get("/agents", self.list_agents),
            web.post("/agents/{agent}/{action:start|stop|restart}", self.control),
            web.get("/agents/{agent}/config", self.config_all),
            web.get("/agents/{agent}/config/{key}", self.config_get),
            web.put("/agents/{agent}/config/{key}", self.config_put),
            # allow_head=False: a HEAD request must not open a stream.
            web.get("/events", self.broker_events, allow_head=False),
            web.post("/agents/{agent}/sessions", self.create_session),
            web.get("/agents/{agent}/sessions", self.list_sessions),
            web.get("/agents/{agent}/sessions/{id}", self.get_session),
            web.delete("/agents/{agent}/sessions/{id}", self.delete_session),
            web.get("/agents/{agent}/sessions/{id}/events", self.session_events, allow_head=False),
            web.post("/agents/{agent}/sessions/{id}/commands", self.command),
            web.post("/agents/{agent}/sessions/{id}/interrupt", self.interrupt),
        ])
        return app

    @web.middleware
    async def _middleware(self, request: web.Request, handler: Handler) -> web.StreamResponse:
        if not credentials_match(request.headers.get("Authorization"), self._username, self._password):
            # The encoded path: a decoded path can hold a line break, which makes a false log line.
            log.warning("Authentication failed: %s %s from %s", request.method, request.rel_url.raw_path,
                        request.remote)
            return error_answer(401, "unauthorized", "authentication failed")
        log.debug("%s %s from %s", request.method, request.path, request.remote)
        try:
            return await handler(request)
        except web.HTTPNotFound:
            return error_answer(404, "not_found", f"no such path: {request.path}")
        except web.HTTPMethodNotAllowed:
            return error_answer(404, "not_found", f"no such operation: {request.method} {request.path}")
        except NotFoundError as exc:
            return error_answer(404, "not_found", str(exc))
        except BadRequestError as exc:
            return error_answer(400, "bad_request", str(exc))
        except AgentError as exc:
            log.info("%s %s: the agent answered %s", request.method, request.path, exc)
            return json_answer("ErrorResponse", exc.body, exc.status)
        except AgentUnavailableError as exc:
            log.warning("%s %s: %s", request.method, request.path, exc)
            return error_answer(503, "agent_unavailable", str(exc))
        except ContainerMissingError as exc:
            return error_answer(404, "not_found", str(exc))
        except DockerUnavailableError as exc:
            # DESIGN.md, section 6.1.1, "Errors": Docker is part of the agent.
            log.warning("%s %s: %s", request.method, request.path, exc)
            return error_answer(503, "agent_unavailable", str(exc))

    def _entry(self, request: web.Request) -> AgentEntry:
        name = request.match_info["agent"]
        entry = self.router.get(name)
        if entry is None:
            raise NotFoundError(f"no agent {name}")
        return entry

    # --- Agents ---------------------------------------------------------------------

    async def _agent_info(self, entry: AgentEntry) -> JsonValue:
        async def status() -> JsonObject | None:
            try:
                return await entry.provider.status()
            except (AgentError, AgentUnavailableError) as exc:
                log.debug("%s: no status: %s", entry.name, exc)
                return None
        container, agent_status = await asyncio.gather(self.docker.inspect(entry.container), status())
        version = agent_status.get("version") if agent_status is not None else None
        return {
            "name": entry.name,
            "provider": entry.provider_name.value,
            "container_state": container.state.value,
            "agent_state": (AgentState.OK if agent_status is not None else AgentState.UNREACHABLE).value,
            # An agent that cannot be reached has the version "" (DESIGN.md, section 6.1.1).
            "version": version if isinstance(version, str) else "",
        }

    async def list_agents(self, request: web.Request) -> web.Response:
        agents = await asyncio.gather(*(self._agent_info(entry) for entry in self.router.entries))
        return json_answer("AgentList", {"agents": list(agents)})

    async def control(self, request: web.Request) -> web.Response:
        entry = self._entry(request)
        action = request.match_info["action"]
        log.info("%s %s requested by %s", action.capitalize(), entry.name, request.remote)
        calls: dict[str, Callable[[str], Awaitable[None]]] = {
            "start": self.docker.start, "stop": self.docker.stop, "restart": self.docker.restart}
        await calls[action](entry.container)
        # The session streams of the agent close after the action. Each client connects again.
        closed = self.proxy.sessions.close_agent(entry.name)
        if closed:
            log.info("%s: %d session streams closed after %s", entry.name, closed, action)
        info = await self.docker.inspect(entry.container)
        if info.state is ContainerState.MISSING:
            raise ContainerMissingError(f"no container {entry.container}")
        return json_answer("AgentResult", {"agent": entry.name, "container_state": info.state.value})

    # --- Configuration ----------------------------------------------------------------

    async def config_all(self, request: web.Request) -> web.Response:
        return json_answer("ConfigValues", await self._entry(request).provider.get_config())

    async def config_get(self, request: web.Request) -> web.Response:
        entry = self._entry(request)
        return json_answer("ConfigValue", await entry.provider.get_config_value(request.match_info["key"]))

    async def config_put(self, request: web.Request) -> web.Response:
        entry = self._entry(request)
        body = await _body(request, "ConfigSetRequest")
        result = await entry.provider.set_config(request.match_info["key"], body["value"])
        return json_answer("ConfigSetResult", result)

    # --- Streams ----------------------------------------------------------------------

    async def broker_events(self, request: web.Request) -> web.StreamResponse:
        if self.closing:
            return error_answer(503, "agent_unavailable", "the broker stops")
        return await self.proxy.serve_broker(request)

    async def session_events(self, request: web.Request) -> web.StreamResponse:
        entry = self._entry(request)
        if self.closing:
            return error_answer(503, "agent_unavailable", "the broker stops")
        return await self.proxy.serve_session(request, entry, request.match_info["id"])

    # --- Sessions ---------------------------------------------------------------------

    async def create_session(self, request: web.Request) -> web.Response:
        entry = self._entry(request)
        body = await _body(request, "SessionCreateRequest")
        return json_answer("Session", await entry.provider.create_session(body), 201)

    async def list_sessions(self, request: web.Request) -> web.Response:
        entry = self._entry(request)
        client = request.query.get("client")
        if not client:
            raise BadRequestError("the query parameter client is required")
        sessions: list[JsonValue] = [s for s in await entry.provider.list_sessions() if s.get("client") == client]
        return json_answer("SessionList", {"sessions": sessions})

    async def get_session(self, request: web.Request) -> web.Response:
        entry = self._entry(request)
        return json_answer("Session", await entry.provider.get_session(request.match_info["id"]))

    async def delete_session(self, request: web.Request) -> web.Response:
        entry = self._entry(request)
        await entry.provider.delete_session(request.match_info["id"])
        # The answer is 204 with no body (DESIGN.md, section 6.1.1).
        return web.Response(status=204)

    async def command(self, request: web.Request) -> web.Response:
        entry = self._entry(request)
        body = await _body(request, "CommandRequest")
        session_id = request.match_info["id"]
        answer = await entry.provider.send_command(session_id, body)
        command_id = body.get("id")
        if answer.get("status") == "accepted" and isinstance(command_id, str):
            self.proxy.note_command(entry.name, session_id, command_id)
        return json_answer("CommandAccepted", answer, 202)

    async def interrupt(self, request: web.Request) -> web.Response:
        entry = self._entry(request)
        return json_answer("SessionResult", await entry.provider.interrupt(request.match_info["id"]))
