"""The event proxy.

- One upstream session stream for each client session stream. The events pass in order,
  with the field ``agent``. The end of one side closes the other side.
- One permanent agent stream for each agent, with reconnect. The broker stream merges
  the agent streams, the Docker lifecycle events and the reachability of each agent.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from aiohttp import web

from agent_broker_client import messages
from agent_broker_client.jsontypes import JsonObject
from agent_broker_client.messages import MessageError

from . import logs
from .docker import ContainerEvent, DockerEngine
from .errors import AgentError, AgentUnavailableError, DockerUnavailableError
from .provider import UpstreamStream
from .router import AgentEntry, Router
from .streams import BrokerHub, ClientStream, SessionStreams

log = logs.get("proxy")

AGENT_REACHABLE = "agent reachable"
AGENT_UNREACHABLE = "agent not reachable"
CONTAINER_TEXTS = {"start": "container started", "restart": "container restarted"}
SSE_HEADERS = {"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def with_agent(agent: str, body: JsonObject) -> JsonObject:
    """The event body with the field ``agent``: the name of the agent in the configuration."""
    return {**body, "agent": agent}


def check_session_event(event: str, body: JsonObject) -> None:
    """Check an event of a session stream against the broker API. Raise MessageError."""
    if event == "log":
        raise MessageError("a log event is not an event of a session stream")
    messages.validate_event(event, body)


def lifecycle(agent: str, text: str) -> JsonObject:
    return {"agent": agent, "kind": "lifecycle", "command_id": None, "text": text}


UPSTREAM_ENDED = "The stream of the agent ended while the command ran."


def command_failed(agent: str, session_id: str, command_id: str | None) -> JsonObject:
    """The notice for an open command when the upstream session stream ends."""
    return {"agent": agent, "session": session_id, "kind": "command_failed", "command_id": command_id,
            "text": UPSTREAM_ENDED}


def container_text(event: ContainerEvent) -> str:
    if event.action == "die":
        return f"container ended, exit code {event.exit_code if event.exit_code is not None else 'unknown'}"
    return CONTAINER_TEXTS[event.action]


class EventProxy:
    """The streams of the broker. ``reconnect_interval`` is the wait between two tries to open an upstream stream."""

    def __init__(self, router: Router, docker: DockerEngine, *, heartbeat: float, queue_limit: int,
                 reconnect_interval: float, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.router = router
        self.docker = docker
        self.heartbeat = heartbeat
        self.queue_limit = queue_limit
        self.reconnect_interval = reconnect_interval
        self._sleep = sleep
        self.hub = BrokerHub()
        self.sessions = SessionStreams()
        self.reachable: dict[str, bool | None] = {entry.name: None for entry in router.entries}
        # The ID of the last accepted command of each session: (agent, session ID) -> command ID.
        self.commands: dict[tuple[str, str], str] = {}
        self._tasks: list[asyncio.Task[None]] = []

    # --- Start and stop --------------------------------------------------------------

    def start(self) -> None:
        """Start the agent streams and the Docker event stream."""
        for entry in self.router.entries:
            self._tasks.append(asyncio.create_task(self._watch_agent(entry), name=f"agent stream {entry.name}"))
        if self.router.entries:
            self._tasks.append(asyncio.create_task(self._watch_docker(), name="docker events"))

    def close_streams(self) -> None:
        """Close each client stream."""
        self.sessions.close_all()
        self.hub.close_all()

    async def close(self) -> None:
        """Close each client stream and each upstream stream."""
        self.close_streams()
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks)

    # --- The broker stream -----------------------------------------------------------

    def publish(self, agent: str, event: str, body: JsonObject) -> None:
        try:
            self.hub.publish(event, with_agent(agent, body))
        except MessageError as exc:
            log.warning("%s: an event of the broker stream is not correct; it is not passed: %s", agent, exc.summary)

    def _set_reachable(self, agent: str, reachable: bool) -> None:
        if self.reachable.get(agent) is reachable:
            return
        self.reachable[agent] = reachable
        text = AGENT_REACHABLE if reachable else AGENT_UNREACHABLE
        log.info("%s: %s", agent, text)
        self.publish(agent, "notice", lifecycle(agent, text))

    async def _watch_agent(self, entry: AgentEntry) -> None:
        """Hold the agent stream of one agent. Connect again RECONNECT_INTERVAL after each end or failure."""
        while True:
            try:
                upstream = await entry.provider.open_agent_stream()
            except (AgentError, AgentUnavailableError) as exc:
                log.debug("%s: the agent stream did not open: %s", entry.name, exc)
                self._set_reachable(entry.name, False)
            else:
                self._set_reachable(entry.name, True)
                try:
                    async for event, body in upstream.events():
                        if event not in ("notice", "log"):
                            log.warning("%s: the event %s is not an event of the agent stream", entry.name, event)
                            continue
                        self.publish(entry.name, event, body)
                    log.info("%s: the agent stream ended", entry.name)
                except AgentUnavailableError as exc:
                    log.info("%s: %s", entry.name, exc)
                except Exception:  # noqa: BLE001 - the task must not end: connect again
                    log.exception("%s: the agent stream failed", entry.name)
                finally:
                    await upstream.close()
            await self._sleep(self.reconnect_interval)

    async def _watch_docker(self) -> None:
        """Hold the Docker event stream. Pass each lifecycle event of an agent container."""
        agents = {entry.container: entry.name for entry in self.router.entries}
        failed = False
        while True:
            try:
                async for event in self.docker.events(agents):
                    failed = False
                    agent = agents.get(event.container)
                    if agent is not None:
                        text = container_text(event)
                        log.info("%s: %s", agent, text)
                        self.publish(agent, "notice", lifecycle(agent, text))
            except DockerUnavailableError as exc:
                if not failed:
                    log.warning("The Docker event stream failed: %s", exc)
                failed = True
            except Exception:  # noqa: BLE001 - the task must not end: connect again
                if not failed:
                    log.exception("The Docker event stream failed")
                failed = True
            await self._sleep(self.reconnect_interval)

    async def serve_broker(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers=SSE_HEADERS)
        try:
            await response.prepare(request)
        except ConnectionError as exc:
            log.info("Broker stream of %s: the client went away: %s", request.remote, exc)
            return response
        stream = ClientStream(request, response, label=f"broker stream of {request.remote}",
                              limit=self.queue_limit, heartbeat=self.heartbeat)
        self.hub.add(stream)
        log.info("%s: open (%d broker streams)", stream.label, self.hub.count)
        try:
            await stream.run()
        finally:
            self.hub.remove(stream)
            log.info("%s: closed", stream.label)
        if stream.overflow:
            stream.abort()
        return response

    # --- Session streams --------------------------------------------------------------

    async def serve_session(self, request: web.Request, entry: AgentEntry, session_id: str) -> web.StreamResponse:
        """Open the upstream stream first. Raise AgentError or AgentUnavailableError if it does not open."""
        upstream = await entry.provider.open_session_stream(session_id)
        stream: ClientStream | None = None
        pump: asyncio.Task[None] | None = None
        response = web.StreamResponse(headers=SSE_HEADERS)
        try:
            try:
                await response.prepare(request)
            except ConnectionError as exc:
                log.info("Session stream %s/%s: the client went away: %s", entry.name, session_id, exc)
                return response
            stream = ClientStream(request, response, label=f"session stream {entry.name}/{session_id}",
                                  limit=self.queue_limit, heartbeat=self.heartbeat)
            self.sessions.add(entry.name, stream)
            log.info("%s: open for %s", stream.label, request.remote)
            pump = asyncio.create_task(self._pump(entry.name, session_id, upstream, stream), name=f"pump {stream.label}")
            await stream.run()
        finally:
            if pump is not None:
                pump.cancel()
                await asyncio.wait([pump])
            await upstream.close()
            if stream is not None:
                self.sessions.remove(entry.name, stream)
                log.info("%s: closed", stream.label)
        if stream is not None and stream.overflow:
            stream.abort()
        return response

    def note_command(self, agent: str, session_id: str, command_id: str) -> None:
        """Keep the ID of an accepted command, for a notice if the upstream stream ends while it runs."""
        self.commands[(agent, session_id)] = command_id

    def forget_session(self, agent: str, session_id: str) -> None:
        """Forget the open command of a deleted session."""
        self.commands.pop((agent, session_id), None)

    async def _pump(self, agent: str, session_id: str, upstream: UpstreamStream, stream: ClientStream) -> None:
        """Pass each upstream event to the client stream. End the client stream when the upstream stream ends.

        If the upstream stream ends in the state working, send command_failed for the open command first.
        """
        state = ""
        try:
            async for event, body in upstream.events():
                out = with_agent(agent, body)
                try:
                    check_session_event(event, out)
                except MessageError as exc:
                    log.warning("%s: an event is not correct; it is not passed: %s", stream.label, exc.summary)
                    continue
                if event == "state":
                    state = str(out.get("state", ""))
                elif event in ("reply", "notice") and out.get("command_id") is not None:
                    # The command has ended: forget it.
                    if self.commands.get((agent, session_id)) == out.get("command_id"):
                        del self.commands[(agent, session_id)]
                if not stream.offer(event, out):
                    return
            log.info("%s: the agent ended the stream", stream.label)
        except AgentUnavailableError as exc:
            log.info("%s: %s", stream.label, exc)
        except Exception:  # noqa: BLE001 - each failure of the upstream stream ends the client stream
            log.exception("%s: the upstream stream failed", stream.label)
        finally:
            if state == "working":
                stream.offer("notice", command_failed(agent, session_id, self.commands.get((agent, session_id))))
            stream.end()
