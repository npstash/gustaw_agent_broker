"""The client library against a fake broker with TLS.

Each test runs its own event loop. The stream intervals are short, so no test sleeps long.
"""
from __future__ import annotations

import asyncio
import base64
import enum
import json
import time
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path

import pytest
from fake_broker import PASSWORD, USERNAME, FakeBroker, Request, TlsFiles, make_tls_files

import agent_broker_client as abc_
from agent_broker_client import messages, models, sse
from agent_broker_client import stream as stream_module
from agent_broker_client.jsontypes import JsonObject, JsonValue

HEARTBEAT = 0.4      # only for the tests of the heartbeat timeout
NO_TIMEOUT = 30.0    # the heartbeat timeout of each other test: a slow machine causes no reconnect
RECONNECT = 0.2


def run(test: Callable[[FakeBroker], Coroutine[object, object, None]], tls: TlsFiles,
        setup: Callable[[FakeBroker], None] | None = None) -> None:
    """Start the fake broker, run the test, stop the broker. Check that no task stays."""
    async def main() -> None:
        broker = FakeBroker(tls)
        if setup is not None:
            setup(broker)
        await broker.start()
        try:
            await asyncio.wait_for(test(broker), 15)
        finally:
            await broker.stop()
        await asyncio.sleep(0.05)
        assert not await pending_tasks()
    asyncio.run(main())


async def pending_tasks() -> list[asyncio.Task[object]]:
    """The tasks that are not done, other than the current one. Wait at most 1 s for them to end.

    A failed TLS handshake can leave an accept task of the fake server. That task is not a task of the client.
    """
    current = asyncio.current_task()
    end = time.monotonic() + 1
    while True:
        pending = [t for t in asyncio.all_tasks()
                   if t is not current and not t.done() and "_accept_connection" not in repr(t)]
        if not pending or time.monotonic() > end:
            return pending
        await asyncio.sleep(0.01)


@pytest.fixture
def tls(tmp_path: Path) -> TlsFiles:
    return make_tls_files(tmp_path / "tls")


def client(broker: FakeBroker, tls: TlsFiles, client_id: str = "client-1", password: str = PASSWORD,
           on_connection: abc_.ConnectionListener | None = None, heartbeat: float = NO_TIMEOUT,
           request_timeout: float = 5) -> abc_.HttpBrokerClient:
    return abc_.HttpBrokerClient(broker.url, USERNAME, password, client_id, cert_file=tls.cert,
                                 heartbeat_timeout=heartbeat, reconnect_interval=RECONNECT,
                                 request_timeout=request_timeout, on_connection=on_connection)


async def until(check: Callable[[], bool], timeout: float = 5) -> None:
    end = time.monotonic() + timeout
    while not check():
        if time.monotonic() > end:
            raise AssertionError("the condition did not become true")
        await asyncio.sleep(0.01)


async def next_item[T](items: AsyncIterator[T], timeout: float = 5) -> T:
    return await asyncio.wait_for(anext(items), timeout)


def last_request(broker: FakeBroker) -> Request:
    """The last request that is not a stream."""
    return [r for r in broker.requests if not r.path.endswith("/events")][-1]


def state(session: abc_.SessionClient) -> abc_.ConnectionState:
    """The connection state. A function call, so that mypy does not keep an old narrowed value."""
    return session.connection_state


def scope(session: abc_.SessionClient) -> JsonObject:
    return {"agent": session.agent, "session": session.session_id}


# --- Operations ---------------------------------------------------------------------

def test_agent_operations(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        async with client(broker, tls) as bc:
            agents = await bc.list_agents()
            assert agents == [
                abc_.AgentInfo("claude", abc_.Provider.AGENT_API, abc_.ContainerState.RUNNING, abc_.AgentState.OK, "1.0"),
                abc_.AgentInfo("opencode", abc_.Provider.OPENCODE, abc_.ContainerState.EXITED,
                               abc_.AgentState.UNREACHABLE, "1.0")]
            assert await bc.start_agent("opencode") == abc_.AgentResult("opencode", abc_.ContainerState.RUNNING)
            assert await bc.stop_agent("opencode") == abc_.AgentResult("opencode", abc_.ContainerState.EXITED)
            assert await bc.restart_agent("claude") == abc_.AgentResult("claude", abc_.ContainerState.RUNNING)
            assert [(r.method, r.path) for r in broker.requests[1:]] == [
                ("POST", "/agents/opencode/start"), ("POST", "/agents/opencode/stop"), ("POST", "/agents/claude/restart")]
    run(test, tls)


def test_config_operations(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        async with client(broker, tls) as bc:
            assert await bc.get_config("claude") == [
                abc_.ConfigValue("claude.model", "opus"), abc_.ConfigValue("log.level", "INFO")]
            assert await bc.get_config_value("claude", "claude.model") == abc_.ConfigValue("claude.model", "opus")
            result = await bc.set_config("claude", "claude.model", "sonnet")
            assert result == abc_.ConfigSetResult("claude.model", "sonnet", abc_.Applied.AT_RESTART)
            assert last_request(broker).body == {"value": "sonnet"}
            assert broker.config["claude"]["claude.model"] == "sonnet"
    run(test, tls)


def test_models_efforts_tools_and_settings(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        async with client(broker, tls) as bc:
            models_ = await bc.list_models("claude")
            assert models_.default == "opus" and [m.id for m in models_.models] == ["opus", "sonnet"]
            efforts = await bc.list_efforts("claude")
            assert efforts.default == "medium" and "high" in efforts.efforts
            policy = abc_.ToolPolicy(allow=("WebSearch", "WebFetch"))
            sc = await bc.create_session("claude", "x", "sonnet", "high", policy)
            assert last_request(broker).body == {"client": "client-1", "workspace": "x", "force": False,
                                                 "model": "sonnet", "effort": "high",
                                                 "tools": {"allow": ["WebSearch", "WebFetch"], "deny": []}}
            assert sc.created is not None and sc.created.tools == policy and sc.created.effort == "high"
            changed = await sc.set_settings(model="opus")
            assert last_request(broker).body == {"model": "opus"}
            assert changed.model == "opus" and changed.effort == "high"
            with pytest.raises(abc_.MessageError):
                await sc.set_settings()
            await sc.close()

    run(test, tls)


def test_session_operations(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        other = broker.add_session("claude", client="other-client")
        async with client(broker, tls) as bc:
            sc = await bc.create_session("claude", "new", "opus", force=True)
            assert last_request(broker).body == {"client": "client-1", "workspace": "new", "force": True, "model": "opus"}
            assert isinstance(sc, abc_.SessionClient)
            assert sc.agent == "claude" and sc.created is not None and sc.created.workspace == "new"
            session = await bc.get_session("claude", sc.session_id)
            assert session.id == sc.session_id and session.client == "client-1" and session.title is None
            assert session.state is abc_.SessionState.IDLE
            assert session.created_at.isoformat() == "2026-11-16T10:00:00+00:00"
            listed = await bc.list_sessions("claude")
            assert [s.id for s in listed] == [sc.session_id]
            assert last_request(broker).query == {"client": "client-1"}
            assert other not in [s.id for s in listed]

            command_id = uuid.uuid4()
            accepted = await sc.send_command(command_id, "hello", abc_.CommandMode.VOICE)
            assert accepted == abc_.CommandAccepted(str(command_id), abc_.CommandStatus.ACCEPTED)
            assert last_request(broker).body == {"id": str(command_id), "text": "hello", "mode": "voice"}
            assert await sc.interrupt() == abc_.SessionResult(abc_.SessionState.IDLE)

            known = bc.session("claude", other)
            assert known.session_id == other and known.connection_state is not abc_.ConnectionState.GONE
            await bc.delete_session("claude", other)
            assert other not in broker.sessions
            await sc.close()
            await known.close()
    run(test, tls)


def test_invalid_input_is_not_sent(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        async with client(broker, tls) as bc:
            sc = bc.session("claude", broker.add_session("claude"))
            count = len(broker.requests)
            with pytest.raises(abc_.MessageError):
                await sc.send_command("not-a-uuid", "hello")
            with pytest.raises(abc_.MessageError):
                await bc.create_session("claude", "", "opus")
            assert all(r.path.endswith("/events") for r in broker.requests[count:])
    run(test, tls)


def test_basic_authentication_is_sent(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        async with client(broker, tls) as bc:
            await bc.list_agents()
        expected = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
        assert last_request(broker).authorization == expected
    run(test, tls)


# --- Errors ------------------------------------------------------------------------

@pytest.mark.parametrize(("status", "code", "error"), [
    (400, "bad_request", abc_.BadRequestError),
    (404, "not_found", abc_.NotFoundError),
    (409, "busy", abc_.BusyError),
    (503, "agent_unavailable", abc_.AgentUnavailableError),
])
def test_error_answers(tls: TlsFiles, status: int, code: str, error: type[abc_.BrokerError]) -> None:
    async def test(broker: FakeBroker) -> None:
        async with client(broker, tls) as bc:
            broker.fail_next = (status, code)
            with pytest.raises(error) as info:
                await bc.list_agents()
            assert info.value.status == status and info.value.error == abc_.ErrorCode(code)
    run(test, tls)


def test_workspace_not_found(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        async with client(broker, tls) as bc:
            with pytest.raises(abc_.WorkspaceNotFoundError) as info:
                await bc.create_session("claude", "missing", "opus")
            assert isinstance(info.value, abc_.NotFoundError)
            assert info.value.error is abc_.ErrorCode.WORKSPACE_NOT_FOUND
            with pytest.raises(abc_.NotFoundError) as other:
                await bc.get_session("nobody", "ses_1")
            assert other.value.error is abc_.ErrorCode.NOT_FOUND
    run(test, tls)


def test_busy_command(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        async with client(broker, tls) as bc:
            sc = bc.session("claude", broker.add_session("claude", state="working"))
            with pytest.raises(abc_.BusyError) as info:
                await sc.send_command(uuid.uuid4(), "hello")
            assert info.value.status == 409 and info.value.error is abc_.ErrorCode.BUSY
    run(test, tls)


def test_wrong_login(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        async with client(broker, tls, password="wrong") as bc:
            with pytest.raises(abc_.UnauthorizedError) as info:
                await bc.list_agents()
            assert info.value.status == 401 and info.value.error is abc_.ErrorCode.UNAUTHORIZED
    run(test, tls)


def test_answer_that_breaks_the_schema(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        broker.agents["claude"] = "up"   # not a container state of the schema
        async with client(broker, tls) as bc:
            with pytest.raises(abc_.InvalidAnswerError):
                await bc.list_agents()
    run(test, tls)


def test_broker_not_reachable(tls: TlsFiles) -> None:
    async def main() -> None:
        broker = FakeBroker(tls)
        await broker.start()
        url = broker.url
        await broker.stop()
        bc = abc_.HttpBrokerClient(url, USERNAME, PASSWORD, "c", cert_file=tls.cert, request_timeout=2)
        try:
            with pytest.raises(abc_.BrokerUnreachableError):
                await bc.list_agents()
        finally:
            await bc.close()
    asyncio.run(main())


def test_tls_checks_the_host_name(tls: TlsFiles, tmp_path: Path) -> None:
    other = make_tls_files(tmp_path / "other", ["other.example"])

    async def test(broker: FakeBroker) -> None:
        async with client(broker, other) as bc:   # the right certificate file, but not the name 127.0.0.1
            with pytest.raises(abc_.BrokerTlsError):
                await bc.list_agents()
    run(test, other)
    context = abc_.client_context(tls.cert)
    assert context.check_hostname


def test_client_has_no_default_certificate_and_default_timeouts() -> None:
    with pytest.raises(TypeError):
        abc_.HttpBrokerClient("https://127.0.0.1:9", USERNAME, PASSWORD, "c")   # type: ignore[call-arg]
    assert (abc_.HEARTBEAT_TIMEOUT_S, abc_.RECONNECT_INTERVAL_S) == (45.0, 5.0)


# --- Streams -----------------------------------------------------------------------

def test_session_stream_gives_state_first_then_live_events(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        async with client(broker, tls) as bc:
            sc = await bc.create_session("claude", "x", "opus")
            items = sc.events()
            first = await next_item(items)
            assert first == abc_.StateEvent("claude", sc.session_id, abc_.SessionState.IDLE)
            assert sc.connection_state is abc_.ConnectionState.CONNECTED
            command_id = str(uuid.uuid4())
            await sc.send_command(command_id, "hi")
            broker.push(sc.session_id, "status", {**scope(sc), "kind": "activity", "text": "Reading"})
            broker.push(sc.session_id, "reply", {**scope(sc), "command_id": command_id, "message": "Done", "language": "en"})
            broker.push(sc.session_id, "notice", {**scope(sc), "kind": "lifecycle", "command_id": None, "text": "New chat"})
            broker.push(sc.session_id, "state", {**scope(sc), "state": "idle"})
            got = [await next_item(items) for _ in range(5)]
            assert got == [
                abc_.StateEvent("claude", sc.session_id, abc_.SessionState.WORKING),
                abc_.StatusEvent("claude", sc.session_id, abc_.StatusKind.ACTIVITY, "Reading"),
                abc_.ReplyEvent("claude", sc.session_id, command_id, "Done", "en"),
                abc_.NoticeEvent("claude", sc.session_id, abc_.NoticeKind.LIFECYCLE, None, "New chat"),
                abc_.StateEvent("claude", sc.session_id, abc_.SessionState.IDLE)]
            await sc.close()
            assert [item async for item in items] == []
            assert state(sc) is abc_.ConnectionState.CLOSED
    run(test, tls)


def test_broker_stream(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        states: list[abc_.ConnectionState] = []
        async with client(broker, tls, on_connection=states.append) as bc:
            assert bc.connection_state is abc_.ConnectionState.NOT_STARTED
            items = bc.events()
            pending = asyncio.ensure_future(next_item(items))
            await until(lambda: len(broker._broker_streams) == 1)
            broker.push_broker("notice", {"agent": "claude", "kind": "lifecycle", "command_id": None, "text": "restarted"})
            broker.push_broker("log", {"agent": "claude", "component": "api", "level": "INFO", "time": "t",
                                       "message": "up"})
            assert await pending == abc_.NoticeEvent("claude", None, abc_.NoticeKind.LIFECYCLE, None, "restarted")
            assert await next_item(items) == abc_.LogEvent("claude", "api", abc_.LogLevel.INFO, "t", "up")
        assert states == [abc_.ConnectionState.CONNECTING, abc_.ConnectionState.CONNECTED, abc_.ConnectionState.CLOSED]
    run(test, tls)


def test_several_session_clients_and_two_broker_clients(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        first = client(broker, tls, "client-a")
        second = client(broker, tls, "client-b")
        sessions = [await first.create_session("claude", "x", "opus"), await first.create_session("claude", "x", "opus"),
                    await second.create_session("claude", "x", "opus")]
        iterators = [s.events() for s in sessions]
        for session, items in zip(sessions, iterators):
            assert isinstance(await next_item(items), abc_.StateEvent)
        for index, session in enumerate(sessions):
            broker.push(session.session_id, "status", {**scope(session), "kind": "activity", "text": f"s{index}"})
        texts = await asyncio.gather(*(next_item(items) for items in iterators))
        assert [t.text for t in texts if isinstance(t, abc_.StatusEvent)] == ["s0", "s1", "s2"]
        assert [s.id for s in await first.list_sessions("claude")] == [s.session_id for s in sessions[:2]]
        assert [s.id for s in await second.list_sessions("claude")] == [sessions[2].session_id]
        await first.close()
        assert all(s.connection_state is abc_.ConnectionState.CLOSED for s in sessions[:2])
        assert sessions[2].connection_state is abc_.ConnectionState.CONNECTED
        await second.close()
    run(test, tls)


def test_heartbeat_timeout_connects_again_and_gives_a_new_state(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        states: list[abc_.ConnectionState] = []
        async with client(broker, tls, heartbeat=HEARTBEAT) as bc:
            session_id = broker.add_session("claude")
            sc = bc.session("claude", session_id, on_connection=states.append)
            items = sc.events()
            assert isinstance(await next_item(items), abc_.StateEvent)
            broker.sessions[session_id]["state"] = "working"
            started = time.monotonic()
            second = await next_item(items)   # the fake broker sends no heartbeat
            assert second == abc_.StateEvent("claude", session_id, abc_.SessionState.WORKING)
            assert time.monotonic() - started >= HEARTBEAT + RECONNECT - 0.05
            assert broker.stream_connects[session_id] == 2
            assert isinstance(sc.last_error, abc_.StreamTimeoutError)
            assert states[:4] == [abc_.ConnectionState.CONNECTING, abc_.ConnectionState.CONNECTED,
                                  abc_.ConnectionState.RECONNECTING, abc_.ConnectionState.CONNECTED]
    run(test, tls)


def test_heartbeat_comments_keep_the_stream(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        async with client(broker, tls, heartbeat=HEARTBEAT) as bc:
            session_id = broker.add_session("claude")
            sc = bc.session("claude", session_id)
            items = sc.events()
            assert isinstance(await next_item(items), abc_.StateEvent)
            await asyncio.sleep(HEARTBEAT * 3)
            assert broker.stream_connects[session_id] == 1
            assert sc.connection_state is abc_.ConnectionState.CONNECTED
    run(test, tls, setup=lambda b: setattr(b, "heartbeat_interval", HEARTBEAT / 8))


def test_closed_stream_connects_again_after_the_interval(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        async with client(broker, tls) as bc:
            session_id = broker.add_session("claude")
            sc = bc.session("claude", session_id)
            items = sc.events()
            assert isinstance(await next_item(items), abc_.StateEvent)
            started = time.monotonic()
            await bc.restart_agent("claude")   # the broker closes each session stream of the agent
            assert isinstance(await next_item(items), abc_.StateEvent)
            assert time.monotonic() - started >= RECONNECT - 0.02
            assert broker.stream_connects[session_id] == 2
            assert sc.last_error is None
    run(test, tls, setup=lambda b: setattr(b, "heartbeat_interval", HEARTBEAT / 4))


def test_unavailable_agent_is_tried_again(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        async with client(broker, tls) as bc:
            session_id = broker.add_session("claude")
            broker.stream_status = 503
            sc = bc.session("claude", session_id)
            items = sc.events()
            await until(lambda: broker.stream_connects[session_id] >= 2)
            assert isinstance(sc.last_error, abc_.AgentUnavailableError)
            assert sc.connection_state in (abc_.ConnectionState.RECONNECTING, abc_.ConnectionState.CONNECTING)
            broker.stream_status = None
            assert isinstance(await next_item(items), abc_.StateEvent)
    run(test, tls)


def test_404_for_a_stream_is_not_retried(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        states: list[abc_.ConnectionState] = []
        async with client(broker, tls) as bc:
            sc = bc.session("claude", "ses_missing", on_connection=states.append)
            with pytest.raises(abc_.SessionGoneError) as info:
                async for _ in sc.events():
                    pass
            assert info.value.status == 404 and info.value.error is abc_.ErrorCode.NOT_FOUND
            assert sc.connection_state is abc_.ConnectionState.GONE
            await asyncio.sleep(RECONNECT * 2)
            assert broker.stream_connects["ses_missing"] == 1
            with pytest.raises(abc_.SessionGoneError):   # a new iterator gives the same result
                await next_item(sc.events())
        assert states == [abc_.ConnectionState.CONNECTING, abc_.ConnectionState.GONE, abc_.ConnectionState.CLOSED]
    run(test, tls)


def test_deleted_session_ends_the_stream(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        async with client(broker, tls) as bc:
            sc = await bc.create_session("claude", "x", "opus")
            items = sc.events()
            assert isinstance(await next_item(items), abc_.StateEvent)
            await bc.delete_session("claude", sc.session_id)
            with pytest.raises(abc_.SessionGoneError):
                await next_item(items)
    run(test, tls)


def test_close_stops_the_reconnect(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        bc = client(broker, tls)
        session_id = broker.add_session("claude")
        broker.stream_status = 503
        sc = bc.session("claude", session_id)
        await until(lambda: broker.stream_connects[session_id] >= 1)
        await sc.close()
        count = broker.stream_connects[session_id]
        await asyncio.sleep(RECONNECT * 2)
        assert broker.stream_connects[session_id] == count
        assert sc.connection_state is abc_.ConnectionState.CLOSED
        await bc.close()
        with pytest.raises(abc_.ClientClosedError):
            await bc.list_agents()
        with pytest.raises(abc_.ClientClosedError):
            bc.session("claude", session_id)
        # run() checks that no task stays after the test.
    run(test, tls)


def test_event_that_breaks_the_schema_is_given_and_the_stream_continues(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        async with client(broker, tls) as bc:
            session_id = broker.add_session("claude")
            sc = bc.session("claude", session_id)
            items = sc.events()
            assert isinstance(await next_item(items), abc_.StateEvent)
            bad_body = json.dumps({"agent": "claude", "session": session_id, "state": "busy"})
            broker.push_raw(session_id, f"event: state\ndata: {bad_body}\n\n".encode())
            broker.push_raw(session_id, b"event: reply\ndata: {not json\n\n")
            broker.push_raw(session_id, b"event: log\ndata: {}\n\n")
            broker.push(session_id, "state", {"agent": "claude", "session": session_id, "state": "idle"})
            invalid = [await next_item(items) for _ in range(3)]
            assert all(isinstance(i, abc_.InvalidEvent) for i in invalid)
            assert [i.event for i in invalid if isinstance(i, abc_.InvalidEvent)] == ["state", "reply", "log"]
            assert isinstance(invalid[0], abc_.InvalidEvent) and isinstance(invalid[0].error, abc_.MessageError)
            assert await next_item(items) == abc_.StateEvent("claude", session_id, abc_.SessionState.IDLE)
            assert broker.stream_connects[session_id] == 1
    run(test, tls, setup=lambda b: setattr(b, "heartbeat_interval", HEARTBEAT / 4))


def test_change_of_session(tls: TlsFiles) -> None:
    """the new session client gets its state; then the program closes the old one."""
    async def test(broker: FakeBroker) -> None:
        async with client(broker, tls) as bc:
            old = await bc.create_session("claude", "x", "opus")
            old_items = old.events()
            assert isinstance(await next_item(old_items), abc_.StateEvent)
            new = await bc.create_session("claude", ".", "opus")
            assert isinstance(await next_item(new.events()), abc_.StateEvent)
            await old.close()
            await until(lambda: broker.open_streams(old.session_id) == 0)
            assert broker.open_streams(new.session_id) == 1
    run(test, tls)


def test_listener_error_does_not_stop_the_stream(tls: TlsFiles) -> None:
    def listener(state: abc_.ConnectionState) -> None:
        raise RuntimeError("listener failed")

    async def test(broker: FakeBroker) -> None:
        async with client(broker, tls) as bc:
            sc = bc.session("claude", broker.add_session("claude"), on_connection=listener)
            assert isinstance(await next_item(sc.events()), abc_.StateEvent)
    run(test, tls)


@pytest.mark.parametrize("stream", ["session", "broker"])
def test_401_for_a_stream_is_not_retried(tls: TlsFiles, stream: str) -> None:
    async def test(broker: FakeBroker) -> None:
        session_id = broker.add_session("claude")
        async with client(broker, tls, password="wrong") as bc:
            source: abc_.SessionClient | abc_.BrokerClient = bc.session("claude", session_id) if stream == "session" else bc
            with pytest.raises(abc_.UnauthorizedError) as info:
                await next_item(source.events())
            assert info.value.status == 401
            assert source.connection_state is abc_.ConnectionState.FAILED
            assert source.last_error is info.value
            await asyncio.sleep(RECONNECT * 2)
            with pytest.raises(abc_.UnauthorizedError):   # a new iterator gives the same result and no new connect
                await next_item(source.events())
            await asyncio.sleep(RECONNECT * 2)
            assert len([r for r in broker.requests if r.path.endswith("/events")]) == 1
    run(test, tls)


def test_tls_verification_failure_for_a_stream_is_not_retried(tls: TlsFiles, tmp_path: Path) -> None:
    other = make_tls_files(tmp_path / "other")   # the same names, but not the certificate of the broker

    async def test(broker: FakeBroker) -> None:
        states: list[abc_.ConnectionState] = []
        async with client(broker, other) as bc:
            sc = bc.session("claude", broker.add_session("claude"), on_connection=states.append)
            with pytest.raises(abc_.BrokerTlsError):
                await next_item(sc.events())
            assert state(sc) is abc_.ConnectionState.FAILED
            await asyncio.sleep(RECONNECT * 2)
            with pytest.raises(abc_.BrokerTlsError):
                await bc.list_agents()
        assert states == [abc_.ConnectionState.CONNECTING, abc_.ConnectionState.FAILED, abc_.ConnectionState.CLOSED]
        assert broker.requests == []
    run(test, tls)


def test_stream_to_a_broker_that_is_not_reachable_is_tried_again(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        session_id = broker.add_session("claude")
        bc = client(broker, tls)
        await broker.stop()
        states: list[abc_.ConnectionState] = []
        sc = bc.session("claude", session_id, on_connection=states.append)
        await until(lambda: states.count(abc_.ConnectionState.RECONNECTING) >= 1)
        assert isinstance(sc.last_error, abc_.BrokerUnreachableError)
        assert not isinstance(sc.last_error, abc_.BrokerTlsError)
        assert sc.last_error.__cause__ is not None   # the cause is not hidden
        await broker.start()   # a new port: the client cannot reach it; the state stays "reconnecting"
        await asyncio.sleep(RECONNECT * 2)
        assert state(sc) is abc_.ConnectionState.RECONNECTING
        await bc.close()
    run(test, tls)


def test_error_answer_of_a_stream_with_no_body_is_tried_again(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        session_id = broker.add_session("claude")
        broker.stream_status, broker.stream_hang = 503, True
        async with client(broker, tls, request_timeout=0.3) as bc:
            sc = bc.session("claude", session_id)
            await until(lambda: broker.stream_connects[session_id] >= 2)
            assert isinstance(sc.last_error, abc_.BrokerUnreachableError)
    run(test, tls)


def test_two_readers_of_the_events_end_after_close(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        async with client(broker, tls) as bc:
            sc = bc.session("claude", broker.add_session("claude"))
            assert isinstance(await next_item(sc.events()), abc_.StateEvent)

            async def read() -> int:
                return len([item async for item in sc.events()])
            readers = [asyncio.ensure_future(read()), asyncio.ensure_future(read())]
            await asyncio.sleep(0.05)   # both readers wait for an event
            await sc.close()
            assert await asyncio.wait_for(asyncio.gather(*readers), 5) == [0, 0]
    run(test, tls)


def test_full_queue_drops_the_connection_and_keeps_the_waiting_events(
        tls: TlsFiles, monkeypatch: pytest.MonkeyPatch) -> None:
    """at STREAM_QUEUE_LIMIT the client drops the connection and connects again
    only after the program has read the waiting events."""
    monkeypatch.setattr(stream_module, "STREAM_QUEUE_LIMIT", 3)

    async def test(broker: FakeBroker) -> None:
        async with client(broker, tls) as bc:
            session_id = broker.add_session("claude")
            sc = bc.session("claude", session_id)
            await until(lambda: broker.open_streams(session_id) == 1)
            for index in range(10):
                broker.push(session_id, "status", {**scope(sc), "kind": "activity", "text": f"s{index}"})
            queue = sc._stream._queue
            await until(lambda: broker.open_streams(session_id) == 0)
            assert queue.qsize() >= 3
            assert isinstance(sc.last_error, abc_.StreamQueueFullError)
            await asyncio.sleep(RECONNECT * 3)
            assert broker.stream_connects[session_id] == 1   # no new connection while events wait
            assert state(sc) is abc_.ConnectionState.RECONNECTING
            items = sc.events()
            waiting = queue.qsize()
            got = [await next_item(items) for _ in range(waiting)]
            assert isinstance(got[0], abc_.StateEvent)
            texts = [e.text for e in got[1:] if isinstance(e, abc_.StatusEvent)]
            assert texts == [f"s{i}" for i in range(len(texts))] and len(texts) == waiting - 1
            assert isinstance(await next_item(items), abc_.StateEvent)   # the new connection
            assert broker.stream_connects[session_id] == 2
    run(test, tls)


def test_agent_control_has_its_own_timeout(tls: TlsFiles) -> None:
    """start, stop and restart wait AGENT_CONTROL_TIMEOUT; each other request keeps its timeout."""
    assert abc_.AGENT_CONTROL_TIMEOUT_S == 60.0

    async def test(broker: FakeBroker) -> None:
        bc = abc_.HttpBrokerClient(broker.url, USERNAME, PASSWORD, "c", cert_file=tls.cert, request_timeout=0.2,
                                   agent_control_timeout=2)
        async with bc:
            broker.delay = 0.5
            assert (await bc.restart_agent("claude")).container_state is abc_.ContainerState.RUNNING
            with pytest.raises(abc_.BrokerUnreachableError):
                await bc.list_agents()
            broker.delay = 0
    run(test, tls)


def test_close_does_not_swallow_the_cancellation_of_its_caller(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        async with client(broker, tls) as bc:
            sc = bc.session("claude", broker.add_session("claude"))
            assert isinstance(await next_item(sc.events()), abc_.StateEvent)
            closing = asyncio.ensure_future(sc.close())
            await asyncio.sleep(0)   # close() now waits for the stream task
            assert not closing.done()
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
            assert state(sc) is abc_.ConnectionState.CLOSED
            # run() checks that the stream task ended.
    run(test, tls)


def test_no_session_client_is_made_during_the_close_of_the_broker_client(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        bc = client(broker, tls)
        first = bc.session("claude", broker.add_session("claude"))
        assert isinstance(await next_item(first.events()), abc_.StateEvent)
        closing = asyncio.ensure_future(bc.close())
        await asyncio.sleep(0)   # close() now waits for the stream task of the session client
        assert not closing.done()
        with pytest.raises(abc_.ClientClosedError):
            bc.session("claude", broker.add_session("claude"))
        await closing
        # run() checks that no stream task stays.
    run(test, tls)


def test_character_in_two_chunks(tls: TlsFiles) -> None:
    async def test(broker: FakeBroker) -> None:
        async with client(broker, tls) as bc:
            session_id = broker.add_session("claude")
            sc = bc.session("claude", session_id)
            items = sc.events()
            assert isinstance(await next_item(items), abc_.StateEvent)
            raw = sse.encode("status", {"agent": "claude", "session": session_id, "kind": "activity", "text": "zażółć"})
            cut = raw.index("ż".encode()) + 1   # in the middle of the character
            broker.push_raw(session_id, raw[:cut])
            await asyncio.sleep(0.1)
            broker.push_raw(session_id, raw[cut:])
            assert await next_item(items) == abc_.StatusEvent("claude", session_id, abc_.StatusKind.ACTIVITY, "zażółć")
    run(test, tls)


# --- Answers that are not correct ---------------------------------------------------

@pytest.mark.parametrize(("status", "raw", "error", "code"), [
    (200, b"<html>not JSON</html>", abc_.InvalidAnswerError, None),
    (200, b"", abc_.InvalidAnswerError, None),
    (200, b"\xff\xfe\xfd", abc_.InvalidAnswerError, None),
    (500, b"<html>error</html>", abc_.InvalidAnswerError, None),
    (401, b"", abc_.UnauthorizedError, None),
    (409, b'{"error": "not_in_the_schema", "message": "x"}', abc_.BusyError, None),
    (404, b'{"error": "workspace_not_found"}', abc_.NotFoundError, None),
    (503, b'{"error": "agent_unavailable", "message": "x"}', abc_.AgentUnavailableError,
     abc_.ErrorCode.AGENT_UNAVAILABLE),
])
def test_answers_that_are_not_correct(tls: TlsFiles, status: int, raw: bytes, error: type[abc_.BrokerError],
                                      code: abc_.ErrorCode | None) -> None:
    async def test(broker: FakeBroker) -> None:
        async with client(broker, tls) as bc:
            broker.raw_next = (status, raw)
            with pytest.raises(error) as info:
                await bc.list_agents()
            assert type(info.value) is error
            assert info.value.status == status and info.value.error is code
    run(test, tls)


# --- SSE ---------------------------------------------------------------------------

def test_sse_decoder() -> None:
    decoder = sse.Decoder()
    assert decoder.feed(": keep-alive\n\n") == []
    assert decoder.feed("event: state\ndata: {\"a\":\n") == []
    assert decoder.feed("data: 1}\n\n") == [("state", "{\"a\":\n1}")]            # two data lines
    assert decoder.feed("event: reply\r\ndata: x\r\n\r\n") == [("reply", "x")]   # CRLF line ends
    assert decoder.feed("event: re") == []                                       # a chunk ends in a line
    assert decoder.feed("ply\ndata:y\n") == []
    assert decoder.feed("\nevent: a\ndata: 1\n\nevent: b\ndata: 2\n\n") == [("reply", "y"), ("a", "1"), ("b", "2")]
    assert decoder.feed("data: z\r") == []                                       # a chunk ends between CR and LF
    assert decoder.feed("\n\r\n") == [("message", "z")]                          # no event field
    assert decoder.feed(": comment\nid: 7\nevent: state\n: comment\ndata: {}\n\n") == [("state", "{}")]
    assert decoder.feed("\n\n") == []
    assert decoder.feed("data: a\r\r\n\n") == [("message", "a\r")]              # only one CR belongs to the line end


def test_sse_encode_and_decode() -> None:
    body: JsonObject = {"agent": "claude", "text": "line 1\nzażółć"}
    assert sse.Decoder().feed(sse.encode("status", body).decode()) == [
        ("status", json.dumps(body, ensure_ascii=False, separators=(",", ":")))]


@pytest.mark.parametrize("event", ["message", "ping", "log", ""])
def test_unknown_event_type_of_a_session_stream(event: str) -> None:
    with pytest.raises(abc_.MessageError):
        models.parse_session_event(event, "{}")
    with pytest.raises(abc_.MessageError):
        models.parse_broker_event("state", json.dumps({"agent": "a", "session": "s", "state": "idle"}))


@pytest.mark.parametrize(("enum_type", "path"), [
    (abc_.SessionState, ["SessionState"]),
    (abc_.ContainerState, ["ContainerState"]),
    (abc_.AgentState, ["AgentState"]),
    (abc_.CommandMode, ["CommandRequest", "properties", "mode"]),
    (abc_.CommandStatus, ["CommandAccepted", "properties", "status"]),
    (abc_.Applied, ["ConfigSetResult", "properties", "applied"]),
    (abc_.ErrorCode, ["ErrorResponse", "properties", "error"]),
    (abc_.StatusKind, ["AgentApiStatusEvent", "properties", "kind"]),
    (abc_.NoticeKind, ["AgentApiNoticeEvent", "properties", "kind"]),
    (abc_.LogLevel, ["AgentApiLogEvent", "properties", "level"]),
    (abc_.Provider, ["AgentList", "properties", "agents", "items", "properties", "provider"]),
])
def test_enums_agree_with_the_schema(enum_type: type[enum.StrEnum], path: list[str]) -> None:
    node: JsonValue = messages.schema()["$defs"]
    for part in path:
        assert isinstance(node, dict)
        node = node[part]
    assert isinstance(node, dict)
    assert node["enum"] == [member.value for member in enum_type]
