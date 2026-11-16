"""The provider ``agent_api``: HTTP to an agent that implements the agent API (DESIGN.md, section 6.2).

The URL of the agent is local. The connection has no TLS and no authentication.
"""
from __future__ import annotations

import asyncio
import codecs
import json
from collections.abc import AsyncIterator, Collection
from urllib.parse import quote

import aiohttp
from yarl import URL

from agent_broker_client import messages, models, sse
from agent_broker_client.jsontypes import JsonObject, JsonValue
from agent_broker_client.messages import MessageError

from . import logs
from .errors import AgentError, AgentUnavailableError
from .provider import AgentProvider, Event, UpstreamStream

log = logs.get("provider")

# The statuses of an agent that pass to the client unchanged (DESIGN.md, section 6.1, "Errors").
PASSED_STATUSES = {400: "bad_request", 404: "not_found", 409: "busy"}


def _part(value: str) -> str:
    """One path segment of a URL, with each special character encoded.

    Raise AgentError (``404``) for ``.`` and ``..``: such a segment changes the path at the agent.
    """
    if value in (".", ".."):
        raise AgentError(404, messages.error_body("not_found", "the name is not valid"))
    return quote(value, safe="")


def _decode(raw: bytes) -> JsonValue:
    try:
        value: JsonValue = json.loads(raw) if raw else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return value


def _error_body(status: int, answer: JsonValue, raw: bytes) -> JsonObject:
    """The ``ErrorResponse`` of an error answer of the agent.

    An answer that is no ErrorResponse gets the error value of its status. Choice of the
    service: the DESIGN does not say what to do with such an answer.
    """
    if isinstance(answer, dict):
        try:
            return messages.validate("ErrorResponse", answer)
        except MessageError:
            pass
    return messages.error_body(PASSED_STATUSES[status], raw[:200].decode("utf-8", "replace"))


def _checked(name: str, answer: JsonValue, where: str) -> JsonObject:
    try:
        checked = messages.validate(name, answer)
    except MessageError as exc:
        # The summary: the text of the error can hold content of the answer.
        raise AgentUnavailableError(
            f"{where}: the answer of the agent does not agree with the schema: {exc.summary}") from None
    if not isinstance(checked, dict):
        raise AgentUnavailableError(f"{where}: the answer of the agent is not an object")
    return checked


class SseStream(UpstreamStream):
    """An SSE stream of an agent. Each event is checked against the agent API."""

    def __init__(self, response: aiohttp.ClientResponse, label: str):
        self._response = response
        self.label = label

    async def events(self) -> AsyncIterator[Event]:
        decoder = sse.Decoder()
        text = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            async for chunk in self._response.content.iter_any():
                for event, data in decoder.feed(text.decode(chunk)):
                    try:
                        body = messages.validate_agent_event(event, models.decode_json(data))
                    except MessageError as exc:
                        # Choice of the service: the broker does not pass an event that breaks the contract.
                        # The summary: the text of the error can hold content of the event.
                        log.warning("%s: an event of the agent is not correct; it is not passed: %s",
                                    self.label, exc.summary)
                        continue
                    if isinstance(body, dict):
                        yield event, body
        except (aiohttp.ClientError, OSError) as exc:
            raise AgentUnavailableError(f"{self.label}: the stream broke: {exc}") from None

    async def close(self) -> None:
        self._response.close()


class AgentApiProvider(AgentProvider):
    """The provider of an agent that implements the agent API. ``url`` is the local URL of the agent."""

    def __init__(self, agent: str, url: str, request_timeout: float):
        self._agent = agent
        self.url = url.rstrip("/")
        self.request_timeout = request_timeout
        self._session: aiohttp.ClientSession | None = None

    @property
    def agent(self) -> str:
        return self._agent

    def _url(self, path: str) -> URL:
        """The URL of a path at the agent. ``encoded``: yarl does not decode and not normalize the path."""
        return URL(self.url + path, encoded=True)

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            # No limit of connections: each client session stream holds one connection.
            self._session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=0))
        return self._session

    async def close(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            await session.close()

    async def _call(self, method: str, path: str, *, body: JsonObject | None = None,
                    ok: Collection[int] = (200,)) -> JsonValue:
        """Send one request. Give the JSON answer. Raise AgentError or AgentUnavailableError."""
        where = f"{self._agent}: {method} {path}"
        data = json.dumps(body, ensure_ascii=False) if body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else None
        try:
            # No redirect: the broker sends a request only to the configured URL of the agent.
            async with self._http().request(method, self._url(path), data=data, headers=headers,
                                            allow_redirects=False,
                                            timeout=aiohttp.ClientTimeout(total=self.request_timeout)) as response:
                status = response.status
                raw = await response.read()
        except TimeoutError:
            raise AgentUnavailableError(f"{where}: no answer in {self.request_timeout} s") from None
        except (aiohttp.ClientError, OSError) as exc:
            raise AgentUnavailableError(f"{where}: the agent is not reachable: {exc}") from None
        answer = _decode(raw)
        if status in PASSED_STATUSES:
            raise AgentError(status, _error_body(status, answer, raw))
        if status not in ok:
            raise AgentUnavailableError(f"{where}: the agent answered {status}")
        return answer

    def _session_body(self, answer: JsonValue, where: str) -> JsonObject:
        """A ``Session`` of the agent with the name of the agent in the configuration.

        Choice of the service: the agent does not know its name in the broker. The broker writes ``agent``.
        """
        if not isinstance(answer, dict):
            raise AgentUnavailableError(f"{where}: the session is not an object")
        return _checked("Session", {**answer, "agent": self._agent}, where)

    async def status(self) -> JsonObject:
        return _checked("AgentStatus", await self._call("GET", "/status"), f"{self._agent}: status")

    async def get_config(self) -> JsonObject:
        return _checked("ConfigValues", await self._call("GET", "/config"), f"{self._agent}: config")

    async def get_config_value(self, key: str) -> JsonObject:
        return _checked("ConfigValue", await self._call("GET", f"/config/{_part(key)}"), f"{self._agent}: config")

    async def set_config(self, key: str, value: JsonValue) -> JsonObject:
        answer = await self._call("PUT", f"/config/{_part(key)}", body={"value": value})
        return _checked("ConfigSetResult", answer, f"{self._agent}: config")

    async def create_session(self, request: JsonObject) -> JsonObject:
        answer = await self._call("POST", "/sessions", body=request, ok=(200, 201))
        return self._session_body(answer, f"{self._agent}: create session")

    async def list_sessions(self) -> list[JsonObject]:
        where = f"{self._agent}: list sessions"
        answer = await self._call("GET", "/sessions")
        items = answer.get("sessions") if isinstance(answer, dict) else None
        if not isinstance(items, list):
            raise AgentUnavailableError(f"{where}: the answer has no list of sessions")
        return [self._session_body(item, where) for item in items]

    async def get_session(self, session_id: str) -> JsonObject:
        answer = await self._call("GET", f"/sessions/{_part(session_id)}")
        return self._session_body(answer, f"{self._agent}: session")

    async def delete_session(self, session_id: str) -> None:
        await self._call("DELETE", f"/sessions/{_part(session_id)}", ok=range(200, 300))

    async def send_command(self, session_id: str, request: JsonObject) -> JsonObject:
        answer = await self._call("POST", f"/sessions/{_part(session_id)}/commands", body=request, ok=(200, 202))
        return _checked("CommandAccepted", answer, f"{self._agent}: command")

    async def interrupt(self, session_id: str) -> JsonObject:
        answer = await self._call("POST", f"/sessions/{_part(session_id)}/interrupt")
        return _checked("SessionResult", answer, f"{self._agent}: interrupt")

    async def _open(self, path: str) -> SseStream:
        where = f"{self._agent}: GET {path}"
        # No read timeout: an agent sends no heartbeat (DESIGN.md, section 6.2).
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=self.request_timeout)
        try:
            async with asyncio.timeout(self.request_timeout):
                response = await self._http().get(self._url(path), timeout=timeout, allow_redirects=False,
                                                  headers={"Accept": "text/event-stream"})
        except TimeoutError:
            raise AgentUnavailableError(f"{where}: no answer in {self.request_timeout} s") from None
        except (aiohttp.ClientError, OSError) as exc:
            raise AgentUnavailableError(f"{where}: the agent is not reachable: {exc}") from None
        if response.status == 200:
            return SseStream(response, where)
        try:
            async with asyncio.timeout(self.request_timeout):
                raw = await response.read()
        except (TimeoutError, aiohttp.ClientError, OSError):
            raw = b""
        finally:
            response.close()
        if response.status in PASSED_STATUSES:
            raise AgentError(response.status, _error_body(response.status, _decode(raw), raw))
        raise AgentUnavailableError(f"{where}: the agent answered {response.status}")

    async def open_session_stream(self, session_id: str) -> UpstreamStream:
        return await self._open(f"/sessions/{_part(session_id)}/events")

    async def open_agent_stream(self) -> UpstreamStream:
        return await self._open("/events")
