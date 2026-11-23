"""A fake Docker engine for the tests of the broker: the few calls of the module docker.

It listens on a Unix socket in a temporary folder of pytest. The path is relative to the
current folder, because a Unix socket path has a maximum of about 100 characters.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field

from aiohttp import web

from agent_broker_client.jsontypes import JsonObject, JsonValue

CLOSE = b""


@dataclass
class FakeDocker:
    """The state of the fake engine. ``containers`` maps a container name to its Docker state."""
    socket_path: str
    containers: dict[str, str] = field(default_factory=lambda: {"claude_code_server": "running"})
    working_dirs: dict[str, str] = field(default_factory=lambda: {"claude_code_server": "/work"})
    actions: list[tuple[str, str]] = field(default_factory=list)
    # Called after each start, stop and restart with (container, action).
    on_action: Callable[[str, str], None] | None = None
    event_connects: int = 0
    # The folders of each container for the exec of mkdir -p and test -d; the commands that ran.
    folders: dict[str, set[str]] = field(default_factory=dict)
    commands: list[tuple[str, list[str]]] = field(default_factory=list)
    _execs: dict[str, int] = field(default_factory=dict)
    _event_queues: list[asyncio.Queue[bytes]] = field(default_factory=list)
    _runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = web.Application()
        app.add_routes([
            web.get("/containers/{name}/json", self._inspect),
            web.post("/containers/{name}/{action:start|stop|restart}", self._control),
            web.get("/events", self._events),
            web.post("/containers/{name}/exec", self._exec_create),
            web.post("/exec/{id}/start", self._exec_start),
            web.get("/exec/{id}/json", self._exec_json),
        ])
        self._runner = web.AppRunner(app, handler_cancellation=True, shutdown_timeout=0.5)
        await self._runner.setup()
        await web.UnixSite(self._runner, self.socket_path).start()

    async def stop(self) -> None:
        for queue in self._event_queues:
            queue.put_nowait(CLOSE)
        runner, self._runner = self._runner, None
        if runner is not None:
            await runner.cleanup()

    @property
    def open_event_streams(self) -> int:
        return len(self._event_queues)

    def emit(self, name: str, action: str, exit_code: str | None = None) -> None:
        """Send one container event to each event stream, in the format of the Docker Engine API."""
        attributes: JsonObject = {"name": name, "image": "test"}
        if exit_code is not None:
            attributes["exitCode"] = exit_code
        body: JsonValue = {"Type": "container", "Action": action, "status": action, "id": "c0ffee",
                           "Actor": {"ID": "c0ffee", "Attributes": attributes}, "scope": "local", "time": 1}
        line = (json.dumps(body) + "\n").encode()
        for queue in self._event_queues:
            queue.put_nowait(line)

    async def _inspect(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        if name not in self.containers:
            return web.json_response({"message": f"No such container: {name}"}, status=404)
        return web.json_response({"Name": "/" + name, "State": {"Status": self.containers[name]},
                                  "Config": {"WorkingDir": self.working_dirs.get(name, "")}})

    async def _exec_create(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        if name not in self.containers:
            return web.json_response({"message": f"No such container: {name}"}, status=404)
        body = await request.json()
        command = [str(part) for part in body["Cmd"]]
        self.commands.append((name, command))
        known = self.folders.setdefault(name, set())
        code = 0
        if command[:2] == ["mkdir", "-p"]:
            known.add(command[2])
        elif command[:2] == ["test", "-d"]:
            code = 0 if command[2] in known else 1
        exec_id = f"exec{len(self._execs) + 1}"
        self._execs[exec_id] = code
        return web.json_response({"Id": exec_id}, status=201)

    async def _exec_start(self, request: web.Request) -> web.Response:
        return web.Response(status=200, body=b"")

    async def _exec_json(self, request: web.Request) -> web.Response:
        return web.json_response({"ExitCode": self._execs[request.match_info["id"]], "Running": False})

    async def _control(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        action = request.match_info["action"]
        if name not in self.containers:
            return web.json_response({"message": f"No such container: {name}"}, status=404)
        self.actions.append((name, action))
        before = self.containers[name]
        if action == "start" and before == "running":
            return web.Response(status=304)
        if action == "stop" and before != "running":
            return web.Response(status=304)
        if action in ("stop", "restart") and before == "running":
            # Docker sends "die" for each end. It also sends "kill" and "stop", which the broker does not use.
            self.emit(name, "die", "0")
            self.emit(name, "stop")
        self.containers[name] = "exited" if action == "stop" else "running"
        if action in ("start", "restart"):
            self.emit(name, "start")
        if action == "restart":
            self.emit(name, "restart")
        if self.on_action is not None:
            self.on_action(name, action)
        return web.Response(status=204)

    async def _events(self, request: web.Request) -> web.StreamResponse:
        self.event_connects += 1
        filters = json.loads(request.query.get("filters", "{}"))
        names = set(filters.get("container", []))
        actions = set(filters.get("event", []))
        response = web.StreamResponse(headers={"Content-Type": "application/json"})
        await response.prepare(request)
        queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._event_queues.append(queue)
        try:
            while True:
                item = await queue.get()
                if item == CLOSE:
                    break
                event = json.loads(item)
                if names and event["Actor"]["Attributes"]["name"] not in names:
                    continue
                if actions and event["Action"] not in actions:
                    continue
                await response.write(item)
        finally:
            self._event_queues.remove(queue)
        return response
