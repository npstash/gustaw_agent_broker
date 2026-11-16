"""A stream of the broker that connects again (DESIGN.md, sections 6.1 and 6.4).

A background task reads the stream and puts each typed event into a queue. The caller
reads the queue with ``items()``. Each received byte counts as data, also a comment line.
With no data for HEARTBEAT_TIMEOUT, the task closes the connection. After a closed or
failed stream, the task waits RECONNECT_INTERVAL and connects again, until ``close()``.
The task does not connect again after a ``404`` of a session stream, a ``401`` or a TLS
verification failure. Then ``items()`` raises the error.

The queue holds STREAM_QUEUE_LIMIT events that the caller did not read (DESIGN.md, section 6.4).
At this limit, the task drops the connection. It keeps the events in the queue, and also the
other events of the last received data. It connects again by the reconnect rule only after
the caller has read each event in the queue. It drops no event that it received.
"""
from __future__ import annotations

import asyncio
import codecs
import enum
import json
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import aiohttp

from . import sse
from .errors import (BrokerError, BrokerTlsError, BrokerUnreachableError, SessionGoneError,
                     StreamQueueFullError, StreamTimeoutError, UnauthorizedError, error_for)
from .jsontypes import JsonValue
from .messages import MessageError
from .models import InvalidEvent, parse_error

log = logging.getLogger("agent_broker.client")

# STREAM_QUEUE_LIMIT (DESIGN.md, section 12): the number of events that the caller did not read, at which
# the stream drops the connection.
STREAM_QUEUE_LIMIT = 100


class ConnectionState(enum.StrEnum):
    """The state of the connection of a stream."""
    NOT_STARTED = "not_started"     # the broker stream before the first call of events()
    CONNECTING = "connecting"       # the first try to connect
    CONNECTED = "connected"         # the broker accepted the stream
    RECONNECTING = "reconnecting"   # the stream closed or failed; the client waits and connects again
    GONE = "gone"                   # 404 for a session stream; the client does not connect again
    FAILED = "failed"               # 401 or a TLS verification failure; the client does not connect again
    CLOSED = "closed"               # the caller closed the client


type ConnectionListener = Callable[[ConnectionState], None]


@dataclass(frozen=True)
class _End:
    """The last item of the queue. ``error`` is set if the stream ended with an error that is not retried."""
    error: BrokerError | None


def _no_retry(error: BrokerError) -> bool:
    """True for an error that a new try to connect cannot repair."""
    if isinstance(error, (SessionGoneError, UnauthorizedError)):
        return True
    return isinstance(error, BrokerTlsError) and isinstance(error.__cause__, aiohttp.ClientConnectorCertificateError)


class EventStream[T]:
    """One SSE stream of the broker with reconnect.

    ``parse(event, data)`` makes the typed event. A MessageError from it gives an InvalidEvent.
    With ``gone_on_404``, a ``404`` ends the stream with SessionGoneError.
    A ``401`` ends the stream with UnauthorizedError. A TLS verification failure ends it with BrokerTlsError.
    Only one caller at a time reads ``items()``. With two callers, each event goes to one of them.
    """

    def __init__(self, http: Callable[[], aiohttp.ClientSession], url: str, parse: Callable[[str, str], T], *,
                 heartbeat_timeout: float, reconnect_interval: float, request_timeout: float,
                 gone_on_404: bool, on_connection: ConnectionListener | None = None):
        self._http = http
        self.url = url
        self._parse = parse
        self.heartbeat_timeout = heartbeat_timeout
        self.reconnect_interval = reconnect_interval
        self.request_timeout = request_timeout
        self.gone_on_404 = gone_on_404
        self._on_connection = on_connection
        self.state = ConnectionState.NOT_STARTED
        self.last_error: BrokerError | None = None
        # ``_read_once`` keeps the limit. The queue itself has none, so that ``_End`` always fits.
        self._queue: asyncio.Queue[T | InvalidEvent | _End] = asyncio.Queue()
        self._drained = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._end: _End | None = None

    @property
    def started(self) -> bool:
        return self.state is not ConnectionState.NOT_STARTED

    def start(self) -> None:
        """Start the background task. Call it in a running event loop."""
        if self.started:
            return
        loop = asyncio.get_running_loop()
        self._set_state(ConnectionState.CONNECTING)
        self._task = loop.create_task(self._run(), name=f"agent-broker-stream {self.url}")

    async def close(self) -> None:
        """Stop the task. ``items()`` ends after the events that are already in the queue."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
        if self.state is not ConnectionState.CLOSED:
            if self.state not in (ConnectionState.GONE, ConnectionState.FAILED):
                self._queue.put_nowait(_End(None))
            self._set_state(ConnectionState.CLOSED)
        if task is not None:
            # wait() does not raise the error of the task. A cancellation of the caller is not lost.
            await asyncio.wait([task])

    async def items(self) -> AsyncIterator[T | InvalidEvent]:
        """Give each event. End when the stream is closed. Raise the error of a stream that is not retried."""
        while True:
            if self._end is not None:
                if self._end.error is not None:
                    raise self._end.error
                return
            item = await self._queue.get()
            if isinstance(item, _End):
                self._end = item
                self._queue.put_nowait(item)   # for each other caller that waits
                continue
            if self._queue.empty():
                self._drained.set()
            yield item

    async def _wait_until_drained(self) -> None:
        """Wait until the caller has read each event in the queue."""
        while not self._queue.empty():
            self._drained.clear()
            await self._drained.wait()

    def _set_state(self, state: ConnectionState) -> None:
        if state is self.state or self.state is ConnectionState.CLOSED:
            return
        self.state = state
        log.debug("Stream %s: %s", self.url, state.value)
        if self._on_connection is not None:
            try:
                self._on_connection(state)
            except Exception:  # noqa: BLE001 - an error of the listener must not stop the stream
                log.exception("The connection listener of %s failed", self.url)

    async def _run(self) -> None:
        while self.state is not ConnectionState.CLOSED:
            try:
                await self._read_once()
                self.last_error = None
            except BrokerError as exc:
                self.last_error = exc
                if _no_retry(exc):
                    log.warning("Stream %s: %s. No new try.", self.url, exc)
                    gone = isinstance(exc, SessionGoneError)
                    self._set_state(ConnectionState.GONE if gone else ConnectionState.FAILED)
                    self._queue.put_nowait(_End(exc))
                    self._task = None
                    return
                log.info("Stream %s: %s", self.url, exc)
                if isinstance(exc, StreamQueueFullError):
                    self._set_state(ConnectionState.RECONNECTING)
                    await self._wait_until_drained()
            except Exception as exc:  # noqa: BLE001 - each other failure also means: connect again
                self.last_error = BrokerError(f"the stream failed: {exc!r}")
                log.exception("Stream %s failed", self.url)
            self._set_state(ConnectionState.RECONNECTING)
            await asyncio.sleep(self.reconnect_interval)

    async def _read_once(self) -> None:
        """Connect one time and read until the stream ends. Raise BrokerError for a failure."""
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=self.request_timeout)
        try:
            async with asyncio.timeout(self.request_timeout):
                response = await self._http().get(self.url, timeout=timeout,
                                                  headers={"Accept": "text/event-stream"})
            async with response:
                if response.status != 200:
                    raise await self._failure(response)
                self._set_state(ConnectionState.CONNECTED)
                decoder = sse.Decoder()
                text = codecs.getincrementaldecoder("utf-8")(errors="replace")
                while True:
                    try:
                        async with asyncio.timeout(self.heartbeat_timeout):
                            chunk = await response.content.readany()
                    except TimeoutError:
                        raise StreamTimeoutError(f"no data for {self.heartbeat_timeout} s") from None
                    if not chunk:
                        return
                    for event, data in decoder.feed(text.decode(chunk)):
                        self._queue.put_nowait(self._item(event, data))
                    if self._queue.qsize() >= STREAM_QUEUE_LIMIT:
                        raise StreamQueueFullError(
                            f"{self._queue.qsize()} events are not read; the client drops the connection")
        except TimeoutError:
            raise BrokerUnreachableError(f"no answer in {self.request_timeout} s") from None
        except aiohttp.ClientSSLError as exc:
            raise BrokerTlsError(f"TLS failed: {exc}") from exc
        except (aiohttp.ClientError, OSError) as exc:
            raise BrokerUnreachableError(f"the stream failed: {exc}") from exc

    def _item(self, event: str, data: str) -> T | InvalidEvent:
        try:
            return self._parse(event, data)
        except MessageError as exc:
            log.warning("Stream %s: an event is not correct: %s", self.url, exc)
            return InvalidEvent(event=event, data=data, error=exc)

    async def _failure(self, response: aiohttp.ClientResponse) -> BrokerError:
        try:
            async with asyncio.timeout(self.request_timeout):
                body: JsonValue = json.loads(await response.text())
        except (json.JSONDecodeError, UnicodeDecodeError, aiohttp.ClientPayloadError):
            body = None
        parsed = parse_error(body)
        code, text = parsed if parsed is not None else (None, response.reason or "")
        if response.status == 404 and self.gone_on_404:
            return SessionGoneError(f"the session is gone: {text}", 404, code)
        return error_for(response.status, code, text)
