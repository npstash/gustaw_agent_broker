"""A fake OpenCode 2.2.7 for the tests of the provider opencode: the routes and the event shapes that the provider uses.

The shapes come from the OpenAPI document of OpenCode 2.2.7 and from live probes on the target host.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
import socket
import time

from aiohttp import web

from agent_broker_client.jsontypes import JsonObject, JsonValue

PASSWORD = "secret"
MODELS: list[JsonValue] = [{"id": "@cf/openai/gpt-oss-20b", "providerID": "cloudflare", "name": "GPT OSS 20B"},
                           {"id": "qwen3:4b-instruct", "providerID": "ollama", "name": "Qwen3 4B"}]


class FakeOpencode:
    def __init__(self) -> None:
        self.sessions: dict[str, JsonObject] = {}
        self.requests: list[tuple[str, str, JsonValue]] = []
        self.prompts: dict[str, list[JsonObject]] = {}
        self.context: dict[str, list[JsonValue]] = {}
        self._streams: list[asyncio.Queue[JsonObject]] = []
        self._next = 0
        self.port = 0
        self._runner: web.AppRunner | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        app = web.Application(middlewares=[self._auth])
        app.add_routes([
            web.get("/api/model", self._models), web.get("/api/model/default", self._default),
            web.post("/api/session", self._create), web.get("/api/session", self._list),
            web.get("/api/session/{id}", self._get), web.delete("/api/session/{id}", self._delete),
            web.post("/api/session/{id}/model", self._model), web.post("/api/session/{id}/prompt", self._prompt),
            web.post("/api/session/{id}/interrupt", self._interrupt), web.get("/api/session/{id}/context", self._ctx),
            web.get("/api/event", self._events),
        ])
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        self._runner = web.AppRunner(app, shutdown_timeout=0.2)
        await self._runner.setup()
        await web.SockSite(self._runner, sock).start()

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    @web.middleware
    async def _auth(self, request: web.Request,
                    handler: Callable[[web.Request], Awaitable[web.StreamResponse]]) -> web.StreamResponse:
        import base64
        expected = "Basic " + base64.b64encode(f"opencode:{PASSWORD}".encode()).decode()
        if request.headers.get("Authorization") != expected:
            return web.json_response({"_tag": "UnauthorizedError", "message": "Authentication required"}, status=401)
        body: JsonValue = await request.json() if request.can_read_body else None
        self.requests.append((request.method, request.path, body))
        request["body"] = body
        return await handler(request)

    def emit(self, kind: str, data: JsonObject) -> None:
        item: JsonObject = {"id": f"evt_{time.time_ns()}", "type": kind, "data": data}
        for queue in self._streams:
            queue.put_nowait(item)

    async def _models(self, request: web.Request) -> web.Response:
        return web.json_response({"location": {"directory": "/w"}, "data": MODELS})

    async def _default(self, request: web.Request) -> web.Response:
        return web.json_response({"location": {"directory": "/w"}, "data": MODELS[0]})

    async def _create(self, request: web.Request) -> web.Response:
        body = request["body"]
        assert isinstance(body, dict)
        self._next += 1
        sid = f"ses_{self._next}"
        now = int(time.time() * 1000)
        self.sessions[sid] = {"id": sid, "projectID": "p", "cost": 0, "tokens": {}, "location": body["location"],
                              "model": {**body["model"], "variant": "default"}, "metadata": body.get("metadata", {}),
                              "permissions": body.get("permissions", []), "time": {"created": now, "updated": now}}
        return web.json_response({"data": self.sessions[sid]})

    async def _list(self, request: web.Request) -> web.Response:
        return web.json_response({"data": list(self.sessions.values()), "cursor": {"previous": None, "next": None}})

    def _found(self, request: web.Request) -> JsonObject | None:
        return self.sessions.get(request.match_info["id"])

    async def _get(self, request: web.Request) -> web.Response:
        found = self._found(request)
        return web.json_response({"data": found}) if found else web.json_response({}, status=404)

    async def _delete(self, request: web.Request) -> web.Response:
        return web.Response(status=204) if self.sessions.pop(request.match_info["id"], None) else \
            web.json_response({}, status=404)

    async def _model(self, request: web.Request) -> web.Response:
        found = self._found(request)
        body = request["body"]
        if found is None or not isinstance(body, dict):
            return web.json_response({}, status=404)
        found["model"] = body["model"]
        return web.Response(status=204)

    async def _prompt(self, request: web.Request) -> web.Response:
        sid = request.match_info["id"]
        body = request["body"]
        assert isinstance(body, dict)
        known = self.prompts.setdefault(sid, [])
        if any(p["id"] == body["id"] for p in known):
            return web.json_response({"_tag": "ConflictError"}, status=409)
        known.append(body)
        return web.json_response({"data": {"id": body["id"], "sessionID": sid, "type": "user"}})

    async def _interrupt(self, request: web.Request) -> web.Response:
        return web.json_response({"interrupted": False})

    async def _ctx(self, request: web.Request) -> web.Response:
        return web.json_response({"data": self.context.get(request.match_info["id"], [])})

    async def _events(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        queue: asyncio.Queue[JsonObject] = asyncio.Queue()
        self._streams.append(queue)
        await response.write(b'data: {"type":"server.connected","data":{}}\n\n')
        try:
            while True:
                item = await queue.get()
                await response.write(f"data: {json.dumps(item)}\n\n".encode())
        finally:
            self._streams.remove(queue)
