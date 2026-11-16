"""Container control: direct HTTP calls on the Docker socket (DESIGN.md, sections 5.1 and 6.1).

The module uses the Docker Engine API with no version prefix in the path. The engine then
uses its newest API version. Only these calls are necessary: inspect, start, stop, restart
of a container, and the event stream of the containers.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from urllib.parse import quote

import aiohttp

from agent_broker_client.jsontypes import JsonObject, JsonValue
from agent_broker_client.models import ContainerState

from . import logs
from .errors import ContainerMissingError, DockerUnavailableError

log = logs.get("docker")

# The host name in the URL. A Unix socket ignores it.
BASE_URL = "http://docker"
# The Docker events that the broker passes as lifecycle events (DESIGN.md, section 6.1, "Streams").
# Docker sends "die" for each end of a container, for a stop and for a crash. The event "stop" is not used:
# the broker gives one event for each end and does not remember which stops it started itself.
LIFECYCLE_ACTIONS = ("start", "restart", "die")

# The Docker states that are not in ContainerState. Choice of the service: the DESIGN does not map them.
# "removing": the container does not run and goes away soon.
_OTHER_STATES = {"removing": ContainerState.EXITED}


@dataclass(frozen=True)
class ContainerInfo:
    """The state and the working directory of a container. ``working_dir`` is empty for a missing container."""
    state: ContainerState
    working_dir: str


@dataclass(frozen=True)
class ContainerEvent:
    """One event of the Docker event stream. ``exit_code`` is set only for ``die``."""
    container: str
    action: str
    exit_code: str | None


def _state(value: JsonValue) -> ContainerState:
    if isinstance(value, str):
        try:
            return ContainerState(value)
        except ValueError:
            if value in _OTHER_STATES:
                return _OTHER_STATES[value]
    raise DockerUnavailableError(f"unknown container state {value!r}")


def _object(value: JsonValue) -> JsonObject:
    return value if isinstance(value, dict) else {}


class DockerEngine:
    """The calls on the Docker socket. ``socket_path`` is the path of the Unix socket."""

    def __init__(self, socket_path: str, *, inspect_timeout: float, control_timeout: float):
        self.socket_path = socket_path
        self.inspect_timeout = inspect_timeout
        self.control_timeout = control_timeout
        self._session: aiohttp.ClientSession | None = None

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=self.socket_path))
        return self._session

    async def close(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            await session.close()

    async def _call(self, method: str, path: str, timeout: float) -> tuple[int, JsonValue]:
        try:
            async with self._http().request(method, BASE_URL + path,
                                            timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                status = response.status
                text = await response.text()
        except TimeoutError:
            raise DockerUnavailableError(f"{method} {path}: no answer in {timeout} s") from None
        except (aiohttp.ClientError, OSError) as exc:
            raise DockerUnavailableError(f"{method} {path}: Docker is not reachable: {exc}") from None
        try:
            body: JsonValue = json.loads(text) if text else None
        except json.JSONDecodeError:
            body = None
        return status, body

    async def inspect(self, name: str) -> ContainerInfo:
        """The state of the container. ``missing`` if no container has the name."""
        status, body = await self._call("GET", f"/containers/{quote(name, safe='')}/json", self.inspect_timeout)
        if status == 404:
            return ContainerInfo(ContainerState.MISSING, "")
        if status != 200:
            raise DockerUnavailableError(f"inspect {name}: Docker answered {status}")
        state = _object(_object(body).get("State")).get("Status")
        working_dir = _object(_object(body).get("Config")).get("WorkingDir")
        return ContainerInfo(_state(state), working_dir if isinstance(working_dir, str) else "")

    async def _control(self, name: str, action: str) -> None:
        status, body = await self._call("POST", f"/containers/{quote(name, safe='')}/{action}", self.control_timeout)
        if status == 404:
            raise ContainerMissingError(f"no container {name}")
        # 304: the container is already in the state.
        if status not in (204, 304):
            message = _object(body).get("message")
            raise DockerUnavailableError(f"{action} {name}: Docker answered {status}: {message}")
        log.info("Container %s: %s", name, action)

    async def start(self, name: str) -> None:
        await self._control(name, "start")

    async def stop(self, name: str) -> None:
        await self._control(name, "stop")

    async def restart(self, name: str) -> None:
        await self._control(name, "restart")

    async def events(self, names: Iterable[str]) -> AsyncIterator[ContainerEvent]:
        """Give each lifecycle event of the named containers. End when Docker ends the stream.

        Raise DockerUnavailableError if the stream does not open or breaks.
        """
        filters = json.dumps({"type": ["container"], "container": sorted(names), "event": list(LIFECYCLE_ACTIONS)})
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=self.inspect_timeout)
        try:
            async with asyncio.timeout(self.inspect_timeout):
                response = await self._http().get(BASE_URL + "/events", params={"filters": filters}, timeout=timeout)
            async with response:
                if response.status != 200:
                    raise DockerUnavailableError(f"events: Docker answered {response.status}")
                async for line in response.content:
                    event = self._event(line)
                    if event is not None:
                        yield event
        except TimeoutError:
            raise DockerUnavailableError(f"events: no answer in {self.inspect_timeout} s") from None
        except (aiohttp.ClientError, OSError) as exc:
            raise DockerUnavailableError(f"events: Docker is not reachable: {exc}") from None

    @staticmethod
    def _event(line: bytes) -> ContainerEvent | None:
        try:
            body: JsonValue = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        event = _object(body)
        action = event.get("Action")
        attributes = _object(_object(event.get("Actor")).get("Attributes"))
        name = attributes.get("name")
        if event.get("Type") != "container" or action not in LIFECYCLE_ACTIONS or not isinstance(name, str):
            return None
        code = attributes.get("exitCode")
        return ContainerEvent(name, str(action), code if isinstance(code, str) and action == "die" else None)
