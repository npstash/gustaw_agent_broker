"""Container control through the host operation service hostops (project gustaw_vaultkeeper).

The container of the broker then needs no Docker socket. Each request is one JSON line on the Unix socket of
hostops, on its own connection. Each answer is one JSON line (DESIGN 8.2 of gustaw_vaultkeeper):

- ``inspect``: ``{"op": "list-containers"}``. The broker selects the container with the exact name.
- ``start``, ``stop``, ``restart``: ``{"op": "control-container", "container": <name>, "action": <action>}``.
- ``events``: one ``{"op": "watch-containers", "containers": [<name>]}`` stream for each container. Each frame is
  ``{"stream": "event", "data": <base64 of a Docker event>}``. The engine merges the streams.

A refusal is ``{"ok": false, "error": "not-granted | invalid-request | failed"}``.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
from collections.abc import AsyncIterator, Iterable

from agent_broker_client.jsontypes import JsonObject, JsonValue
from agent_broker_client.models import ContainerState

from . import logs
from .docker import ContainerEngine, ContainerEvent, ContainerInfo, container_state, parse_event
from .errors import ContainerMissingError, DockerUnavailableError

log = logs.get("hostops")

# The largest answer line: list-containers gives each container of the host.
LINE_MAX = 16 * 1024 * 1024
# The error of hostops for a granted request that it cannot do, e.g. a container that does not exist.
FAILED = "failed"

type _Item = ContainerEvent | DockerUnavailableError | None


class HostopsError(DockerUnavailableError):
    """hostops refused a request. ``code`` is its error: not-granted, invalid-request or failed."""

    def __init__(self, op: str, code: str):
        super().__init__(f"{op}: hostops answered {code}")
        self.code = code


def _answer(line: bytes, op: str) -> JsonObject:
    """The JSON object of one answer line. Raise DockerUnavailableError."""
    if not line.endswith(b"\n"):
        raise DockerUnavailableError(f"{op}: hostops gave no complete answer")
    try:
        value: JsonValue = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise DockerUnavailableError(f"{op}: the answer of hostops is not JSON") from None
    if not isinstance(value, dict):
        raise DockerUnavailableError(f"{op}: the answer of hostops is not an object")
    return value


def _frame_event(frame: JsonObject) -> ContainerEvent | None:
    """The lifecycle event of a watch-containers frame, or None for another frame."""
    data = frame.get("data")
    if frame.get("stream") != "event" or not isinstance(data, str):
        return None
    try:
        body: JsonValue = json.loads(base64.b64decode(data, validate=True))
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
        log.debug("A frame of watch-containers is not a Docker event")
        return None
    return parse_event(body)


def _request_line(request: JsonObject) -> bytes:
    return json.dumps(request, separators=(",", ":")).encode() + b"\n"


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()


class HostopsEngine(ContainerEngine):
    """The container control on the socket of hostops. ``socket_path`` is the path of the Unix socket."""

    def __init__(self, socket_path: str, *, inspect_timeout: float, control_timeout: float):
        self.socket_path = socket_path
        self.inspect_timeout = inspect_timeout
        self.control_timeout = control_timeout

    async def close(self) -> None:
        """Nothing to close: each request has its own connection, and each stream closes with its iterator."""

    async def _open(self, op: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            async with asyncio.timeout(self.inspect_timeout):
                return await asyncio.open_unix_connection(self.socket_path, limit=LINE_MAX)
        except TimeoutError:
            raise DockerUnavailableError(f"{op}: no connection to hostops in {self.inspect_timeout} s") from None
        except OSError as exc:
            raise DockerUnavailableError(f"{op}: hostops at {self.socket_path} is not reachable: {exc}") from None

    async def _request(self, request: JsonObject, timeout: float) -> JsonValue:
        """Send one request and give the ``result`` of the answer. Raise HostopsError for a refusal."""
        op = str(request["op"])
        reader, writer = await self._open(op)
        try:
            async with asyncio.timeout(timeout):
                writer.write(_request_line(request))
                await writer.drain()
                line = await reader.readline()
        except TimeoutError:
            raise DockerUnavailableError(f"{op}: no answer of hostops in {timeout} s") from None
        except (OSError, ValueError) as exc:     # ValueError: the line is longer than LINE_MAX
            raise DockerUnavailableError(f"{op}: the connection to hostops failed: {exc}") from None
        finally:
            await _close(writer)
        answer = _answer(line, op)
        if answer.get("ok") is not True:
            error = answer.get("error")
            raise HostopsError(op, error if isinstance(error, str) else "an error")
        return answer.get("result")

    async def inspect(self, name: str) -> ContainerInfo:
        """The state of the container with exactly this name, from list-containers."""
        result = await self._request({"op": "list-containers"}, self.inspect_timeout)
        if not isinstance(result, list):
            raise DockerUnavailableError("list-containers: hostops gave no list")
        for item in result:
            names = item.get("Names") if isinstance(item, dict) else None
            if isinstance(item, dict) and isinstance(names, list) and f"/{name}" in names:
                return ContainerInfo(container_state(item.get("State")), "")
        return ContainerInfo(ContainerState.MISSING, "")

    async def _control(self, name: str, action: str) -> None:
        request: JsonObject = {"op": "control-container", "container": name, "action": action}
        try:
            await self._request(request, self.control_timeout)
        except HostopsError as exc:
            # hostops gives "failed" for a container that does not exist, and for a Docker error.
            if exc.code == FAILED and (await self.inspect(name)).state is ContainerState.MISSING:
                raise ContainerMissingError(f"no container {name}") from None
            raise
        log.info("Container %s: %s", name, action)

    async def start(self, name: str) -> None:
        await self._control(name, "start")

    async def stop(self, name: str) -> None:
        await self._control(name, "stop")

    async def restart(self, name: str) -> None:
        await self._control(name, "restart")

    async def events(self, names: Iterable[str]) -> AsyncIterator[ContainerEvent]:
        """Merge one watch-containers stream for each container: one grant entry of hostops gives one container.

        End when one stream ends. Raise DockerUnavailableError when one stream does not open, is refused or breaks.
        The other streams then close too.
        """
        wanted = sorted(set(names))
        if not wanted:
            return
        queue: asyncio.Queue[_Item] = asyncio.Queue()
        tasks = [asyncio.create_task(self._watch(name, queue), name=f"hostops watch {name}") for name in wanted]
        try:
            while True:
                item = await queue.get()
                if item is None:
                    return
                if isinstance(item, DockerUnavailableError):
                    raise item
                yield item
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.wait(tasks)

    async def _watch(self, name: str, queue: asyncio.Queue[_Item]) -> None:
        """Put each lifecycle event of one container into ``queue``. Put None at the end, or the error."""
        op = "watch-containers"
        try:
            reader, writer = await self._open(op)
        except DockerUnavailableError as exc:
            queue.put_nowait(exc)
            return
        try:
            writer.write(_request_line({"op": op, "containers": [name]}))
            await writer.drain()
            while True:
                line = await reader.readline()
                if not line:
                    queue.put_nowait(None)
                    return
                answer = _answer(line, op)
                if "stream" in answer:
                    event = _frame_event(answer)
                    if event is not None and event.container == name:
                        queue.put_nowait(event)
                    continue
                if answer.get("ok") is not True:
                    error = answer.get("error")
                    queue.put_nowait(HostopsError(f"{op} {name}", error if isinstance(error, str) else "an error"))
                else:
                    queue.put_nowait(None)
                return
        except DockerUnavailableError as exc:
            queue.put_nowait(exc)
        except (OSError, ValueError) as exc:     # ValueError: the line is longer than LINE_MAX
            queue.put_nowait(DockerUnavailableError(f"{op} {name}: the stream of hostops failed: {exc}"))
        finally:
            await _close(writer)
