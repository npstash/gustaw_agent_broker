"""The provider opencode against a fake OpenCode 2.2.7."""
from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable, Coroutine

import pytest
from fake_opencode import PASSWORD, FakeOpencode

from agent_broker_client.jsontypes import JsonObject, JsonValue
from agent_broker_service.errors import AgentError
from agent_broker_service.opencode import OpencodeProvider, command_id, message_id, permission_rules
from agent_broker_service.provider import Event

type Test = Callable[[FakeOpencode, OpencodeProvider], Coroutine[object, object, None]]


def run(test: Test) -> None:
    async def main() -> None:
        fake = FakeOpencode()
        await fake.start()
        provider = OpencodeProvider("opencode", fake.url, PASSWORD, 5.0, "/home/opencode/workspace",
                                    lambda text: "en" if text else None)
        try:
            await test(fake, provider)
        finally:
            await provider.close()
            await fake.stop()
    asyncio.run(main())


def request(model: str = "cloudflare/@cf/openai/gpt-oss-20b", **extra: JsonValue) -> JsonObject:
    body: JsonObject = {"client": "scanner", "workspace": "example_project", "force": True, "model": model}
    body.update(extra)
    return body


def test_message_and_command_ids_map_both_ways() -> None:
    cid = str(uuid.uuid4())
    assert message_id(cid).startswith("msg_") and command_id(message_id(cid)) == cid
    assert command_id("msg_2078b330f001jq84ORwcQ5w3Vw") is None and command_id("other") is None


def test_permission_rules_of_a_tool_policy() -> None:
    env = [{"action": "read", "resource": "*.env", "effect": "deny"},
           {"action": "read", "resource": "*.env.*", "effect": "deny"}]
    assert permission_rules(None) == env
    assert permission_rules({"allow": None, "deny": ["shell"]}) == [
        {"action": "shell", "resource": "*", "effect": "deny"}, *env]
    assert permission_rules({"allow": ["webfetch", "websearch"], "deny": []}) == [
        {"action": "*", "resource": "*", "effect": "deny"}, {"action": "webfetch", "resource": "*", "effect": "allow"},
        {"action": "websearch", "resource": "*", "effect": "allow"}, *env]


def test_models_efforts_and_a_new_session() -> None:
    async def test(fake: FakeOpencode, provider: OpencodeProvider) -> None:
        models = await provider.list_models()
        assert models["default"] == "cloudflare/@cf/openai/gpt-oss-20b"
        assert {"id": "ollama/qwen3:4b-instruct", "name": "Qwen3 4B"} in models["models"]  # type: ignore[operator]
        assert await provider.list_efforts() == {"efforts": ["auto"], "default": "auto"}
        policy: JsonObject = {"allow": ["webfetch", "websearch"], "deny": []}
        session = await provider.create_session(request(tools=policy))
        method, path, body = fake.requests[-1]
        assert (method, path) == ("POST", "/api/session") and isinstance(body, dict)
        assert body["location"] == {"directory": "/home/opencode/workspace/example_project"}
        assert body["model"] == {"id": "@cf/openai/gpt-oss-20b", "providerID": "cloudflare"}
        permissions = body["permissions"]
        assert isinstance(permissions, list) and permissions[0] == {"action": "*", "resource": "*", "effect": "deny"}
        assert session["model"] == "cloudflare/@cf/openai/gpt-oss-20b" and session["effort"] == "auto"
        assert session["tools"] == policy and session["client"] == "scanner" and session["workspace"] == "example_project"
        assert session["agent"] == "opencode" and session["state"] == "idle"
        assert [s["id"] for s in await provider.list_sessions()] == [session["id"]]

    run(test)


def test_invalid_settings_and_workspaces_are_refused() -> None:
    async def test(fake: FakeOpencode, provider: OpencodeProvider) -> None:
        for bad in (request("cloudflare/no-such-model"), request(effort="high")):
            with pytest.raises(AgentError) as info:
                await provider.create_session(bad)
            assert info.value.status == 400 and info.value.body["error"] == "invalid_setting"
        with pytest.raises(AgentError) as info:
            await provider.create_session(request(workspace="a/b"))
        assert info.value.status == 400
        assert not fake.sessions

    run(test)


def test_settings_change_the_model_and_refuse_a_working_session() -> None:
    async def test(fake: FakeOpencode, provider: OpencodeProvider) -> None:
        session = await provider.create_session(request())
        sid = str(session["id"])
        changed = await provider.set_session_settings(sid, {"model": "ollama/qwen3:4b-instruct"})
        assert changed["model"] == "ollama/qwen3:4b-instruct"
        assert fake.requests[-2][1] == f"/api/session/{sid}/model"
        fake.sessions[sid]["time"] = {"created": 1, "busy": 3, "idle": 2}
        with pytest.raises(AgentError) as info:
            await provider.set_session_settings(sid, {"model": "cloudflare/@cf/openai/gpt-oss-20b"})
        assert info.value.status == 409

    run(test)


def test_commands_and_duplicates() -> None:
    async def test(fake: FakeOpencode, provider: OpencodeProvider) -> None:
        sid = str((await provider.create_session(request()))["id"])
        cid = str(uuid.uuid4())
        assert await provider.send_command(sid, {"id": cid, "text": "hello"}) == {"id": cid, "status": "accepted"}
        assert fake.prompts[sid] == [{"id": message_id(cid), "text": "hello"}]
        assert await provider.send_command(sid, {"id": cid, "text": "hello"}) == {"id": cid, "status": "duplicate"}

    run(test)


async def take(events: list[Event], stream_events: object, count: int) -> None:
    iterator = stream_events.__aiter__()  # type: ignore[attr-defined]
    while len(events) < count:
        events.append(await asyncio.wait_for(iterator.__anext__(), 5))


def test_the_session_stream_maps_the_events_of_opencode() -> None:
    async def test(fake: FakeOpencode, provider: OpencodeProvider) -> None:
        sid = str((await provider.create_session(request()))["id"])
        cid = str(uuid.uuid4())
        stream = await provider.open_session_stream(sid)
        events: list[Event] = []
        reader = asyncio.create_task(take(events, stream.events(), 7))
        await asyncio.sleep(0.2)
        other: JsonObject = {"sessionID": "ses_other"}
        fake.emit("session.execution.started", other)                  # another session: no event
        fake.emit("session.inbox.delivered", {"sessionID": sid, "inboxID": message_id(cid)})
        fake.emit("session.execution.started", {"sessionID": sid})
        fake.emit("session.tool.called", {"sessionID": sid, "id": "t1"})
        fake.emit("session.text.ended", {"sessionID": sid, "text": "PONG"})
        fake.emit("session.execution.succeeded", {"sessionID": sid})
        second = str(uuid.uuid4())
        fake.emit("session.inbox.delivered", {"sessionID": sid, "inboxID": message_id(second)})
        fake.emit("session.execution.failed", {"sessionID": sid, "error": {"message": "rate limit"}})
        await asyncio.wait_for(reader, 5)
        await stream.close()
        kinds = [e[0] for e in events]
        assert kinds == ["state", "state", "status", "reply", "state", "notice", "state"]
        assert events[0][1]["state"] == "idle" and events[1][1]["state"] == "working"
        assert events[3][1] == {"session": sid, "command_id": cid, "message": "PONG", "language": "en"}
        assert events[5][1]["kind"] == "command_failed" and events[5][1]["command_id"] == second

    run(test)


def test_a_new_stream_gives_the_last_reply_again() -> None:
    async def test(fake: FakeOpencode, provider: OpencodeProvider) -> None:
        sid = str((await provider.create_session(request()))["id"])
        cid = str(uuid.uuid4())
        now = int(time.time() * 1000)
        fake.context[sid] = [{"id": message_id(cid), "type": "user", "text": "hi"},
                             {"id": "msg_a", "type": "assistant", "time": {"completed": now},
                              "content": [{"type": "reasoning", "text": "x"}, {"type": "text", "text": "Done"}]}]
        stream = await provider.open_session_stream(sid)
        events: list[Event] = []
        await take(events, stream.events(), 2)
        await stream.close()
        assert events[1] == ("reply", {"session": sid, "command_id": cid, "message": "Done", "language": "en"})

    run(test)
