"""The broker client and the session client over HTTPS (DESIGN.md, sections 6.1.1 and 6.1.3).

The library does not read a vault. The caller gives the login.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from urllib.parse import quote

import aiohttp

from . import messages, models
from .api import BrokerClient, SessionClient
from .errors import (BrokerError, BrokerTlsError, BrokerUnreachableError, ClientClosedError, InvalidAnswerError,
                     error_for)
from .jsontypes import JsonObject, JsonValue
from .messages import MessageError
from .models import (AgentInfo, AgentResult, BrokerEvent, CommandAccepted, CommandMode, ConfigSetResult,
                     ConfigValue, InvalidEvent, Session, SessionEvent, SessionResult)
from .stream import ConnectionListener, ConnectionState, EventStream
from .tls import DEFAULT_CERT_FILE, client_context

HEARTBEAT_TIMEOUT_S = 45.0     # HEARTBEAT_TIMEOUT (DESIGN.md, section 12)
RECONNECT_INTERVAL_S = 5.0     # RECONNECT_INTERVAL
# The DESIGN gives no limit for a request. Choice of the library: 15 s.
REQUEST_TIMEOUT_S = 15.0
AGENT_CONTROL_TIMEOUT_S = 60.0   # AGENT_CONTROL_TIMEOUT: start, stop and restart of an agent


def _part(value: str) -> str:
    """One path segment of a URL, with each special character encoded."""
    return quote(value, safe="")


class HttpBrokerClient(BrokerClient):
    """The broker client.

    ``url`` is the URL of the broker, e.g. ``https://broker-host:38443``. ``username`` and
    ``password`` are the login of the broker. ``cert_file`` is the certificate of the broker
    (the trust anchor). Make the object in a running event loop or outside of one; the
    connections open at the first call.
    """

    def __init__(self, url: str, username: str, password: str, client_id: str, *,
                 cert_file: Path | str = DEFAULT_CERT_FILE,
                 heartbeat_timeout: float = HEARTBEAT_TIMEOUT_S,
                 reconnect_interval: float = RECONNECT_INTERVAL_S,
                 request_timeout: float = REQUEST_TIMEOUT_S,
                 agent_control_timeout: float = AGENT_CONTROL_TIMEOUT_S,
                 on_connection: ConnectionListener | None = None):
        if not client_id:
            raise ValueError("the client ID must not be empty")
        self.url = url.rstrip("/")
        self._client_id = client_id
        self._auth_header = aiohttp.encode_basic_auth(username, password)
        self._ssl = client_context(cert_file)
        self.heartbeat_timeout = heartbeat_timeout
        self.reconnect_interval = reconnect_interval
        self.request_timeout = request_timeout
        self.agent_control_timeout = agent_control_timeout
        self._http_session: aiohttp.ClientSession | None = None
        self._closed = False
        self._sessions: set[HttpSessionClient] = set()
        self._stream: EventStream[BrokerEvent] = self.make_stream(
            "/events", models.parse_broker_event, gone_on_404=False, on_connection=on_connection)

    # --- Internal API of the session clients ----------------------------------------

    def http(self) -> aiohttp.ClientSession:
        """The HTTP session of this client. Make it at the first call."""
        if self._closed:
            raise ClientClosedError("the broker client is closed")
        if self._http_session is None:
            self._http_session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=self._ssl), headers={"Authorization": self._auth_header})
        return self._http_session

    def make_stream[T](self, path: str, parse: Callable[[str, str], T], *, gone_on_404: bool,
                       on_connection: ConnectionListener | None) -> EventStream[T]:
        return EventStream(self.http, self.url + path, parse, heartbeat_timeout=self.heartbeat_timeout,
                           reconnect_interval=self.reconnect_interval, request_timeout=self.request_timeout,
                           gone_on_404=gone_on_404, on_connection=on_connection)

    def forget(self, session: HttpSessionClient) -> None:
        self._sessions.discard(session)

    async def call[R](self, method: str, path: str, parse: Callable[[JsonValue], R], body: JsonObject | None = None,
                      params: Mapping[str, str] | None = None, timeout_s: float | None = None) -> R:
        """Send one request. Return the typed answer. Raise a BrokerError for an error answer.

        ``timeout_s`` replaces the request timeout for this request.
        """
        limit = timeout_s if timeout_s is not None else self.request_timeout
        timeout = aiohttp.ClientTimeout(total=limit)
        data = json.dumps(body, ensure_ascii=False) if body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else None
        try:
            async with self.http().request(method, self.url + path, data=data, headers=headers, params=params,
                                           timeout=timeout) as response:
                status = response.status
                text = await response.text()
        except TimeoutError:
            raise BrokerUnreachableError(f"{method} {path}: no answer in {limit} s") from None
        except aiohttp.ClientSSLError as exc:
            raise BrokerTlsError(f"{method} {path}: TLS failed: {exc}") from exc
        except (aiohttp.ClientError, OSError) as exc:
            raise BrokerUnreachableError(f"{method} {path}: the broker is not reachable: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise InvalidAnswerError(f"{method} {path}: the answer is not text: {exc}", status) from exc
        answer: JsonValue
        try:
            answer = json.loads(text) if text else None
        except json.JSONDecodeError:
            answer = None
        if not 200 <= status < 300:
            parsed = models.parse_error(answer)
            code, message = parsed if parsed is not None else (None, text[:200])
            raise error_for(status, code, f"{method} {path}: {message}")
        try:
            return parse(answer)
        except MessageError as exc:
            raise InvalidAnswerError(f"{method} {path}: {exc}", status) from None

    # --- BrokerClient --------------------------------------------------------------

    @property
    def client_id(self) -> str:
        return self._client_id

    @property
    def connection_state(self) -> ConnectionState:
        return self._stream.state

    @property
    def last_error(self) -> BrokerError | None:
        return self._stream.last_error

    async def list_agents(self) -> list[AgentInfo]:
        return await self.call("GET", "/agents", models.parse_agent_list)

    async def _control(self, agent: str, action: str) -> AgentResult:
        return await self.call("POST", f"/agents/{_part(agent)}/{action}", models.parse_agent_result,
                               timeout_s=self.agent_control_timeout)

    async def start_agent(self, agent: str) -> AgentResult:
        return await self._control(agent, "start")

    async def stop_agent(self, agent: str) -> AgentResult:
        return await self._control(agent, "stop")

    async def restart_agent(self, agent: str) -> AgentResult:
        return await self._control(agent, "restart")

    async def get_config(self, agent: str) -> list[ConfigValue]:
        return await self.call("GET", f"/agents/{_part(agent)}/config", models.parse_config_values)

    async def get_config_value(self, agent: str, key: str) -> ConfigValue:
        return await self.call("GET", f"/agents/{_part(agent)}/config/{_part(key)}", models.parse_config_value)

    async def set_config(self, agent: str, key: str, value: JsonValue) -> ConfigSetResult:
        body = messages.validate("ConfigSetRequest", {"value": value})
        return await self.call("PUT", f"/agents/{_part(agent)}/config/{_part(key)}",
                               models.parse_config_set_result, body)

    async def events(self) -> AsyncIterator[BrokerEvent | InvalidEvent]:
        if self._closed:
            return
        self._stream.start()
        async for item in self._stream.items():
            yield item

    async def list_sessions(self, agent: str) -> list[Session]:
        return await self.call("GET", f"/agents/{_part(agent)}/sessions", models.parse_session_list,
                               params={"client": self._client_id})

    async def create_session(self, agent: str, workspace: str, force: bool = False, *,
                             on_connection: ConnectionListener | None = None) -> HttpSessionClient:
        request: JsonObject = {"client": self._client_id, "workspace": workspace, "force": force}
        body = messages.validate("SessionCreateRequest", request)
        created = await self.call("POST", f"/agents/{_part(agent)}/sessions", models.parse_session, body)
        # The DESIGN does not say if Session.agent can differ from the name in the URL. The client uses the URL name.
        return self.session(agent, created.id, on_connection=on_connection, created=created)

    def session(self, agent: str, session_id: str, *, on_connection: ConnectionListener | None = None,
                created: Session | None = None) -> HttpSessionClient:
        if self._closed:
            raise ClientClosedError("the broker client is closed")
        client = HttpSessionClient(self, agent, session_id, on_connection=on_connection, created=created)
        self._sessions.add(client)
        return client

    async def get_session(self, agent: str, session_id: str) -> Session:
        return await self.call("GET", f"/agents/{_part(agent)}/sessions/{_part(session_id)}", models.parse_session)

    async def delete_session(self, agent: str, session_id: str) -> None:
        # The DESIGN gives no body for the answer. The client accepts each 2xx answer and ignores the body.
        await self.call("DELETE", f"/agents/{_part(agent)}/sessions/{_part(session_id)}", lambda answer: None)

    async def close(self) -> None:
        if self._closed:
            return
        # Set the mark first: a call during the close makes no new session client and no new connection.
        self._closed = True
        try:
            await asyncio.gather(*(session.close() for session in list(self._sessions)))
            await self._stream.close()
        finally:
            http, self._http_session = self._http_session, None
            if http is not None:
                await http.close()


class HttpSessionClient(SessionClient):
    """The session client. Make it with ``HttpBrokerClient.create_session`` or ``HttpBrokerClient.session``.

    ``created`` is the ``Session`` of ``create_session``, else None.
    """

    def __init__(self, broker: HttpBrokerClient, agent: str, session_id: str, *,
                 on_connection: ConnectionListener | None = None, created: Session | None = None):
        self._broker = broker
        self._agent = agent
        self._session_id = session_id
        self.created = created
        self._base = f"/agents/{_part(agent)}/sessions/{_part(session_id)}"
        self._stream: EventStream[SessionEvent] = broker.make_stream(
            self._base + "/events", models.parse_session_event, gone_on_404=True, on_connection=on_connection)
        self._stream.start()

    @property
    def agent(self) -> str:
        return self._agent

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def connection_state(self) -> ConnectionState:
        return self._stream.state

    @property
    def last_error(self) -> BrokerError | None:
        return self._stream.last_error

    async def send_command(self, command_id: str | uuid.UUID, text: str,
                           mode: CommandMode = CommandMode.TEXT) -> CommandAccepted:
        request: JsonObject = {"id": str(command_id), "text": text, "mode": mode.value}
        body = messages.validate("CommandRequest", request)
        return await self._broker.call("POST", self._base + "/commands", models.parse_command_accepted, body)

    async def interrupt(self) -> SessionResult:
        return await self._broker.call("POST", self._base + "/interrupt", models.parse_session_result)

    async def events(self) -> AsyncIterator[SessionEvent | InvalidEvent]:
        async for item in self._stream.items():
            yield item

    async def close(self) -> None:
        await self._stream.close()
        self._broker.forget(self)
