"""The streams to the clients (DESIGN.md, section 6.1.1, "Streams" and "Heartbeat").

Each client stream has a send queue of STREAM_QUEUE_LIMIT events. A writer task writes the
events in order. After HEARTBEAT_INTERVAL with no data, it writes a comment line. When an
event comes and the queue is full, the broker closes the stream. It drops no event silently:
it writes the number of lost events to the log.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass

import aiohttp
from aiohttp import web

from agent_broker_client import messages, sse
from agent_broker_client.jsontypes import JsonObject

from . import logs
from .provider import Event

log = logs.get("proxy")


@dataclass(frozen=True)
class _End:
    """The last item of a queue: the writer ends after the items before it."""


_END = _End()


class ClientStream:
    """One SSE stream to one client.

    ``offer`` adds an event. ``end`` ends the stream after the waiting events. ``close``
    ends it at once. ``run`` writes until the stream ends, the client goes away or a write fails.
    """

    def __init__(self, request: web.Request, response: web.StreamResponse, *, label: str, limit: int,
                 heartbeat: float):
        self.request = request
        self.response = response
        self.label = label
        self.limit = limit
        self.heartbeat = heartbeat
        self.closed = False
        self.overflow = False
        self._items: deque[Event | _End] = deque()
        self._wake = asyncio.Event()
        self._writer: asyncio.Task[None] | None = None

    @property
    def waiting(self) -> int:
        return len(self._items)

    def offer(self, event: str, body: JsonObject) -> bool:
        """Add an event. If the queue is full, close the stream and give False."""
        if self.closed:
            return False
        if len(self._items) >= self.limit:
            log.warning("%s: %d events wait; the client reads too slowly. The broker closes the stream.",
                        self.label, len(self._items))
            self.overflow = True
            self.close(lost=1)   # the event of this call is lost too
            return False
        self._items.append((event, body))
        self._wake.set()
        return True

    def end(self) -> None:
        if not self.closed:
            self._items.append(_END)
            self._wake.set()

    def close(self, lost: int = 0) -> None:
        """End the stream at once. ``lost`` is the number of lost events that are not in the queue."""
        if self.closed:
            return
        self.closed = True
        lost += sum(1 for item in self._items if not isinstance(item, _End))
        if lost:
            log.warning("%s: closed with %d events that the client did not get", self.label, lost)
        self._items.clear()
        if self._writer is not None:
            self._writer.cancel()

    def abort(self) -> None:
        """Drop the connection. The client does not read, so a normal end can wait for a long time."""
        transport = self.request.transport
        if transport is not None:
            transport.abort()

    async def run(self) -> None:
        if self.closed:
            return
        writer = asyncio.create_task(self._write(), name=f"write {self.label}")
        self._writer = writer
        try:
            # wait() does not raise the cancellation of the writer. A cancellation of this task is not lost.
            await asyncio.wait([writer])
        finally:
            if not writer.done():
                writer.cancel()
                await asyncio.wait([writer])
            # The stream is at its end. Write the number of events that wait to the log.
            self.close()

    async def _write(self) -> None:
        try:
            while True:
                if self._items:
                    item = self._items.popleft()
                    if isinstance(item, _End):
                        return
                    await self.response.write(sse.encode(*item))
                    continue
                self._wake.clear()
                try:
                    async with asyncio.timeout(self.heartbeat):
                        await self._wake.wait()
                except TimeoutError:
                    await self.response.write(sse.COMMENT)
        except (ConnectionError, aiohttp.ClientConnectionResetError) as exc:
            log.info("%s: the write failed: %s", self.label, exc)


class BrokerHub:
    """The client streams of the broker stream. Each one gets each event."""

    def __init__(self) -> None:
        self._streams: set[ClientStream] = set()

    @property
    def count(self) -> int:
        return len(self._streams)

    def add(self, stream: ClientStream) -> None:
        self._streams.add(stream)

    def remove(self, stream: ClientStream) -> None:
        self._streams.discard(stream)

    def publish(self, event: str, body: JsonObject) -> None:
        """Send a checked event of the broker API to each stream."""
        messages.validate_event(event, body)
        for stream in list(self._streams):
            stream.offer(event, body)

    def close_all(self) -> None:
        for stream in list(self._streams):
            stream.close()


class SessionStreams:
    """The client session streams of each agent."""

    def __init__(self) -> None:
        self._streams: dict[str, set[ClientStream]] = {}

    def count(self, agent: str | None = None) -> int:
        if agent is not None:
            return len(self._streams.get(agent, ()))
        return sum(len(streams) for streams in self._streams.values())

    def add(self, agent: str, stream: ClientStream) -> None:
        self._streams.setdefault(agent, set()).add(stream)

    def remove(self, agent: str, stream: ClientStream) -> None:
        streams = self._streams.get(agent)
        if streams is not None:
            streams.discard(stream)
            if not streams:
                del self._streams[agent]

    def close_agent(self, agent: str) -> int:
        """Close each session stream of the agent. Give the number of streams."""
        streams = list(self._streams.get(agent, ()))
        for stream in streams:
            stream.close()
        return len(streams)

    def close_all(self) -> None:
        for agent in list(self._streams):
            self.close_agent(agent)
