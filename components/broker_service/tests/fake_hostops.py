"""A fake hostops for the tests of the broker: the operations list-containers, control-container and watch-containers.

The request and answer lines have the shapes of DESIGN 8.2 of gustaw_vaultkeeper and of its dispatcher:
- ``{"op": "list-containers"}`` gives ``{"ok": true, "result": [<Docker container without Env>]}``;
- ``{"op": "control-container", "container", "action"}`` gives ``{"ok": true, "result": {}}``. hostops gives
  ``failed`` for a container that does not exist;
- ``{"op": "watch-containers", "containers": [<one name>]}`` gives frames ``{"stream": "event", "data": <base64>}``
  until the caller closes the connection. A request for more than one container is refused: one grant entry gives
  one container;
- a refusal is ``{"ok": false, "error": "not-granted | invalid-request | failed"}``.

It listens on a Unix socket in the current folder: a Unix socket path has a maximum of about 100 characters.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
from dataclasses import dataclass, field

from agent_broker_client.jsontypes import JsonObject, JsonValue

ACTIONS = ("start", "stop", "restart")


@dataclass
class FakeHostops:
    """``containers`` maps a container name to its Docker state. ``control`` and ``watch`` are the granted names."""
    socket_path: str
    containers: dict[str, str] = field(default_factory=lambda: {"claude_code_server": "running", "opencode": "running",
                                                                "claude_code_server_old": "exited"})
    control: set[str] = field(default_factory=lambda: {"claude_code_server", "opencode"})
    watch: set[str] = field(default_factory=lambda: {"claude_code_server", "opencode"})
    list_granted: bool = True
    # A raw answer line instead of the answer of the next request, e.g. a line that is not JSON.
    raw_answer: bytes | None = None
    requests: list[JsonObject] = field(default_factory=list)
    _watchers: dict[str, list[asyncio.StreamWriter]] = field(default_factory=dict)
    _server: asyncio.AbstractServer | None = None
    _handlers: set[asyncio.Task[None]] = field(default_factory=set)

    async def start(self) -> None:
        self._server = await asyncio.start_unix_server(self._serve, self.socket_path)

    async def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
        for writers in self._watchers.values():
            for writer in writers:
                writer.close()
        for task in list(self._handlers):
            task.cancel()
        if self._handlers:
            await asyncio.wait(self._handlers)
        if server is not None:
            await server.wait_closed()

    def open_watches(self, name: str) -> int:
        return len(self._watchers.get(name, []))

    def emit(self, name: str, action: str, exit_code: str | None = None, **extra: str) -> None:
        """Send one Docker event of the container to each of its watch streams."""
        attributes: JsonObject = {"name": name, "image": "test", **extra}
        if exit_code is not None:
            attributes["exitCode"] = exit_code
        body: JsonValue = {"Type": "container", "Action": action, "status": action, "id": "c0ffee",
                           "Actor": {"ID": "c0ffee", "Attributes": attributes}, "scope": "local", "time": 1}
        frame = {"stream": "event", "data": base64.b64encode(json.dumps(body).encode()).decode()}
        for writer in self._watchers.get(name, []):
            writer.write(json.dumps(frame).encode() + b"\n")

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._handlers.add(task)
        try:
            line = await reader.readline()
            if not line:
                return
            request = json.loads(line)
            assert isinstance(request, dict)
            self.requests.append(request)
            if self.raw_answer is not None:
                writer.write(self.raw_answer)
                self.raw_answer = None
            elif request.get("op") == "watch-containers":
                await self._watch(request, reader, writer)
            else:
                writer.write(json.dumps(self._answer(request)).encode() + b"\n")
            await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            if task is not None:
                self._handlers.discard(task)

    @staticmethod
    def _error(code: str) -> JsonObject:
        return {"ok": False, "error": code}

    def _answer(self, request: JsonObject) -> JsonObject:
        op = request.get("op")
        if op == "list-containers":
            if set(request) != {"op"}:
                return self._error("invalid-request")
            if not self.list_granted:
                return self._error("not-granted")
            result: list[JsonValue] = [
                {"Id": f"id-{name}", "Names": [f"/{name}"], "Image": "test", "State": state, "Status": "Up 1 second"}
                for name, state in self.containers.items()]
            return {"ok": True, "result": result}
        if op == "control-container":
            name, action = request.get("container"), request.get("action")
            if set(request) != {"op", "container", "action"} or action not in ACTIONS or not isinstance(name, str):
                return self._error("invalid-request")
            if name not in self.control:
                return self._error("not-granted")
            if name not in self.containers:
                return self._error("failed")
            self._act(name, str(action))
            return {"ok": True, "result": {}}
        return self._error("invalid-request")

    def _act(self, name: str, action: str) -> None:
        """Change the state as Docker does, and send its events."""
        running = self.containers[name] == "running"
        if action in ("stop", "restart") and running:
            self.emit(name, "die", "0")
            self.emit(name, "stop")
        if action == "start" and running or action == "stop" and not running:
            return
        self.containers[name] = "exited" if action == "stop" else "running"
        if action in ("start", "restart"):
            self.emit(name, "start")
        if action == "restart":
            self.emit(name, "restart")

    async def _watch(self, request: JsonObject, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        names = request.get("containers")
        if set(request) != {"op", "containers"} or not isinstance(names, list) or not names:
            writer.write(json.dumps(self._error("invalid-request")).encode() + b"\n")
            return
        if len(set(names)) != 1 or names[0] not in self.watch:
            writer.write(json.dumps(self._error("not-granted")).encode() + b"\n")
            return
        name = str(names[0])
        self._watchers.setdefault(name, []).append(writer)
        try:
            while await reader.read(4096):     # the stream ends when the caller closes the connection
                pass
        finally:
            self._watchers[name].remove(writer)
