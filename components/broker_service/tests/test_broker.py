"""The broker service end to end (DESIGN.md, sections 6.1.1, 6.1.2 and 7): the real broker with TLS, driven
by the real client library, against a fake agent and a fake Docker engine.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from fake_agent import OWN_NAME, FakeAgent
from fake_docker import FakeDocker
from rig import (CONTAINER, BrokerEvents, Rig, fake_secrets, make_tls, next_item, pending_tasks, run,
                 settings_for, until)

import agent_broker_client as abc_
from agent_broker_client.jsontypes import JsonObject
from agent_broker_service.app import Broker
from agent_broker_service.provider import Event, UpstreamStream
from agent_broker_service.streams import ClientStream
from agent_broker_service.tls import server_context


@pytest.fixture
def in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run the test in tmp_path. The Docker socket path is relative to it."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def broker_notices(items: list[abc_.BrokerEvent | abc_.InvalidEvent]) -> list[str]:
    return [e.text for e in items if isinstance(e, abc_.NoticeEvent)]


# --- Agents and container control ------------------------------------------------------

def test_agent_list_asks_docker_and_the_agent_at_each_call(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as bc:
            [agent] = await bc.list_agents()
            assert (agent.name, agent.provider, agent.container_state, agent.agent_state, agent.version) == (
                "claude", abc_.Provider.AGENT_API, abc_.ContainerState.RUNNING, abc_.AgentState.OK, "2.0")
            rig.docker.containers[CONTAINER] = "paused"
            rig.agent.version = "2.1"
            [agent] = await bc.list_agents()
            assert (agent.container_state, agent.version) == (abc_.ContainerState.PAUSED, "2.1")
            await rig.agent.stop()
            [agent] = await bc.list_agents()
            assert (agent.agent_state, agent.version) == (abc_.AgentState.UNREACHABLE, "")
    run(test, in_tmp)


def test_missing_container(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        del rig.docker.containers[CONTAINER]
        async with rig.client() as bc:
            [agent] = await bc.list_agents()
            assert agent.container_state is abc_.ContainerState.MISSING
            with pytest.raises(abc_.NotFoundError):
                await bc.start_agent("claude")
    run(test, in_tmp)


def test_docker_not_reachable_gives_503(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        await rig.docker.stop()
        async with rig.client() as bc:
            with pytest.raises(abc_.AgentUnavailableError):
                await bc.list_agents()
            with pytest.raises(abc_.AgentUnavailableError):
                await bc.restart_agent("claude")
    run(test, in_tmp)


def test_start_stop_and_restart_close_the_session_streams(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        session_id = rig.agent.add_session()
        async with rig.client() as bc:
            sc = bc.session("claude", session_id)
            items = sc.events()
            assert isinstance(await next_item(items), abc_.StateEvent)
            result = await bc.restart_agent("claude")
            assert (result.agent, result.container_state) == ("claude", abc_.ContainerState.RUNNING)
            assert rig.docker.actions == [(CONTAINER, "restart")]
            assert isinstance(await next_item(items), abc_.StateEvent)   # the client connected again
            await until(lambda: rig.agent.session_connects[session_id] == 2 and rig.agent.session_ends[session_id] == 1)

            assert (await bc.stop_agent("claude")).container_state is abc_.ContainerState.EXITED
            assert isinstance(await next_item(items), abc_.StateEvent)
            assert (await bc.start_agent("claude")).container_state is abc_.ContainerState.RUNNING
            assert isinstance(await next_item(items), abc_.StateEvent)
            assert rig.agent.session_connects[session_id] == 4
    run(test, in_tmp)


def test_restart_gives_lifecycle_events_on_the_broker_stream(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as bc:
            seen: list[abc_.BrokerEvent | abc_.InvalidEvent] = []
            events = BrokerEvents(bc)
            await until(lambda: rig.broker.proxy.hub.count == 1)
            await bc.restart_agent("claude")
            while "container restarted" not in broker_notices(seen):
                seen.append(await events.next())
            assert broker_notices(seen) == [
                "container ended, exit code 0", "container started", "container restarted"]
            assert all(isinstance(e, abc_.NoticeEvent) and e.agent == "claude" and e.session is None
                       and e.kind is abc_.NoticeKind.LIFECYCLE for e in seen)
    run(test, in_tmp)


def test_stop_gives_one_lifecycle_event_for_the_end(in_tmp: Path) -> None:
    """Docker sends "die" and "stop" for a stop. The broker reports one event for the end (X1)."""
    async def test(rig: Rig) -> None:
        async with rig.client() as bc:
            events = BrokerEvents(bc)
            await until(lambda: rig.broker.proxy.hub.count == 1)
            await bc.stop_agent("claude")
            await bc.start_agent("claude")
            seen = [await events.next() for _ in range(2)]
            assert broker_notices(seen) == ["container ended, exit code 0", "container started"]
            with pytest.raises(asyncio.TimeoutError):
                await events.next(timeout=0.3)
    run(test, in_tmp)


def test_disabled_agent_is_not_served(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as bc:
            assert [a.name for a in await bc.list_agents()] == ["claude"]
        for method, path in (("GET", "/agents/opencode/config"), ("POST", "/agents/opencode/start"),
                             ("GET", "/agents/opencode/sessions?client=c"), ("POST", "/agents/opencode/sessions")):
            status, _ = await rig.raw(method, path)
            assert status == 404, (method, path)
        assert rig.docker.actions == []   # the container of a disabled agent is not touched
    run(test, in_tmp)


def test_ended_container_gives_a_lifecycle_event(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as bc:
            events = BrokerEvents(bc)
            await until(lambda: rig.broker.proxy.hub.count == 1)
            rig.docker.emit(CONTAINER, "die", "137")
            rig.docker.emit("other_container", "die", "1")   # not a container of an agent
            item = await events.next()
            assert isinstance(item, abc_.NoticeEvent)
            assert (item.agent, item.text) == ("claude", "container ended, exit code 137")
    run(test, in_tmp)


def test_broker_stream_merges_the_agent_stream_and_the_reachability(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as bc:
            events = BrokerEvents(bc)
            await until(lambda: rig.broker.proxy.hub.count == 1)
            rig.agent.push_agent("log", {"component": "api", "level": "INFO", "time": "10:00:00", "message": "up"})
            rig.agent.push_agent("notice", {"kind": "lifecycle", "command_id": None, "text": "new chat"})
            log_event = await events.next()
            assert isinstance(log_event, abc_.LogEvent) and (log_event.agent, log_event.message) == ("claude", "up")
            notice = await events.next()
            assert isinstance(notice, abc_.NoticeEvent) and (notice.agent, notice.text) == ("claude", "new chat")
            await rig.agent.stop()
            notice = await events.next()
            assert isinstance(notice, abc_.NoticeEvent) and notice.text == "agent not reachable"
            await rig.agent.start()
            notice = await events.next()
            assert isinstance(notice, abc_.NoticeEvent) and notice.text == "agent reachable"
    run(test, in_tmp)


# --- Configuration ---------------------------------------------------------------------------

def test_config_operations(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as bc:
            values = await bc.get_config("claude")
            assert [(v.key, v.value) for v in values] == [("claude.model", "opus"), ("log.level", "INFO")]
            assert (await bc.get_config_value("claude", "claude.model")).value == "opus"
            result = await bc.set_config("claude", "claude.model", "sonnet")
            assert (result.key, result.value, result.applied) == ("claude.model", "sonnet", abc_.Applied.NOW)
            assert rig.agent.config["claude.model"] == "sonnet"
            with pytest.raises(abc_.NotFoundError) as caught:
                await bc.get_config_value("claude", "no.key")
            assert caught.value.error is abc_.ErrorCode.NOT_FOUND
            with pytest.raises(abc_.NotFoundError):
                await bc.get_config("nobody")
    run(test, in_tmp)


# --- Sessions --------------------------------------------------------------------------------

def test_session_operations(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        other = rig.agent.add_session(client="client-2")
        rig.agent.add_session(client=None)   # a session that another tool made
        async with rig.client() as bc:
            sc = await bc.create_session("claude", "/x")
            assert sc.created is not None
            assert (sc.created.agent, sc.created.client, sc.created.workspace) == ("claude", "client-1", "x")
            session = await bc.get_session("claude", sc.session_id)
            # The broker writes the name of the configuration, not the name that the agent sends.
            assert (session.agent, rig.agent.sessions[sc.session_id]["agent"]) == ("claude", OWN_NAME)
            assert [s.id for s in await bc.list_sessions("claude")] == [sc.session_id]
            await sc.close()
            await bc.delete_session("claude", sc.session_id)
            assert sc.session_id not in rig.agent.sessions
            assert await bc.list_sessions("claude") == []
            with pytest.raises(abc_.NotFoundError):
                await bc.get_session("claude", sc.session_id)
            with pytest.raises(abc_.NotFoundError):
                await bc.delete_session("claude", "no_session")
            assert other in rig.agent.sessions
    run(test, in_tmp)


def test_session_list_needs_the_client(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        status, text = await rig.raw("GET", "/agents/claude/sessions")
        assert status == 400 and json.loads(text)["error"] == "bad_request"
        status, text = await rig.raw("GET", "/agents/claude/sessions?client=")
        assert status == 400
        rig.agent.add_session(client="c2")
        status, text = await rig.raw("GET", "/agents/claude/sessions?client=c2")
        assert status == 200 and [s["client"] for s in json.loads(text)["sessions"]] == ["c2"]
    run(test, in_tmp)


def test_workspace_errors_pass_through(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as bc:
            with pytest.raises(abc_.WorkspaceNotFoundError) as caught:
                await bc.create_session("claude", "new_folder")
            assert caught.value.status == 404 and caught.value.error is abc_.ErrorCode.WORKSPACE_NOT_FOUND
            sc = await bc.create_session("claude", "new_folder", force=True)
            assert sc.created is not None and sc.created.workspace == "new_folder"
            with pytest.raises(abc_.BadRequestError):
                await bc.create_session("claude", "../outside")
            await sc.close()
    run(test, in_tmp)


def test_commands_busy_duplicate_and_interrupt(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        session_id = rig.agent.add_session()
        async with rig.client() as bc:
            sc = bc.session("claude", session_id)
            first = uuid.uuid4()
            assert (await sc.send_command(first, "Run the tests")).status is abc_.CommandStatus.ACCEPTED
            assert (await sc.send_command(first, "Run the tests")).status is abc_.CommandStatus.DUPLICATE
            with pytest.raises(abc_.BusyError) as caught:
                await sc.send_command(uuid.uuid4(), "Other")
            assert caught.value.status == 409
            assert (await sc.interrupt()).session_state is abc_.SessionState.IDLE
            await sc.close()
    run(test, in_tmp)


def test_bad_requests(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        session_id = rig.agent.add_session()
        cases = [
            ("POST", f"/agents/claude/sessions/{session_id}/commands", "not json", 400, "bad_request"),
            ("POST", f"/agents/claude/sessions/{session_id}/commands", '{"id": "x", "text": "a"}', 400, "bad_request"),
            ("POST", "/agents/claude/sessions", '{"workspace": "x"}', 400, "bad_request"),
            ("PUT", "/agents/claude/config/claude.model", '{"other": 1}', 400, "bad_request"),
            ("GET", "/agents/nobody/sessions?client=c", None, 404, "not_found"),
            ("GET", "/nothing", None, 404, "not_found"),
            ("DELETE", "/agents", None, 404, "not_found"),
        ]
        for method, path, data, status, error in cases:
            got, text = await rig.raw(method, path, data=data)
            assert (got, json.loads(text)["error"]) == (status, error), (method, path)
        assert not [r for r in rig.agent.requests if r[0] != "GET"]   # nothing went to the agent
    run(test, in_tmp)


# --- Authentication and errors ------------------------------------------------------------------

def test_authentication(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        for header in (None, rig.auth_header("wrong"), "Basic !!!", "Bearer x"):
            status, text = await rig.raw("GET", "/agents", authorization=header)
            assert (status, json.loads(text)["error"]) == (401, "unauthorized")
        status, _ = await rig.raw("GET", "/events", authorization=None)
        assert status == 401
        async with rig.client(password="wrong") as bc:
            with pytest.raises(abc_.UnauthorizedError):
                await bc.list_agents()
            with pytest.raises(abc_.UnauthorizedError):
                async for _ in bc.events():
                    pass
    run(test, in_tmp)


def test_agent_that_is_down_gives_503(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        session_id = rig.agent.add_session()
        await rig.agent.stop()
        async with rig.client() as bc:
            with pytest.raises(abc_.AgentUnavailableError) as caught:
                await bc.list_sessions("claude")
            assert caught.value.status == 503 and caught.value.error is abc_.ErrorCode.AGENT_UNAVAILABLE
            sc = bc.session("claude", session_id)
            for call in (sc.send_command(uuid.uuid4(), "x"), sc.interrupt(), bc.get_config("claude"),
                         bc.get_session("claude", session_id), bc.create_session("claude", "x")):
                with pytest.raises(abc_.AgentUnavailableError):
                    await call
            await until(lambda: isinstance(sc.last_error, abc_.AgentUnavailableError))
            assert sc.connection_state is abc_.ConnectionState.RECONNECTING   # the client tries again
            await rig.agent.start()
            assert isinstance(await next_item(sc.events()), abc_.StateEvent)
            await sc.close()
    run(test, in_tmp)


def test_answer_of_the_agent_that_breaks_the_schema_gives_503(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as bc:
            rig.agent.raw_next = (200, b'{"values": [{"key": 1}]}')
            with pytest.raises(abc_.AgentUnavailableError):
                await bc.get_config("claude")
            rig.agent.raw_next = (500, b"boom")
            with pytest.raises(abc_.AgentUnavailableError):
                await bc.get_config("claude")
            rig.agent.raw_next = (409, b"no body")
            with pytest.raises(abc_.BusyError):
                await bc.get_config("claude")
    run(test, in_tmp)


# --- Session streams ---------------------------------------------------------------------------

def test_session_stream_gives_state_first_then_the_events_in_order_with_agent(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        session_id = rig.agent.add_session()
        async with rig.client() as bc:
            sc = bc.session("claude", session_id)
            items = sc.events()
            first = await next_item(items)
            assert isinstance(first, abc_.StateEvent)
            assert (first.agent, first.session, first.state) == ("claude", session_id, abc_.SessionState.IDLE)
            command = str(uuid.uuid4())
            for index in range(20):
                rig.agent.push(session_id, "status", {"kind": "activity", "text": f"s{index}"})
            rig.agent.push(session_id, "reply", {"command_id": command, "message": "Done", "language": "en"})
            rig.agent.push(session_id, "notice", {"kind": "command_failed", "command_id": command, "text": "x"})
            got = [await next_item(items) for _ in range(22)]
            assert [e.text for e in got[:20] if isinstance(e, abc_.StatusEvent)] == [f"s{i}" for i in range(20)]
            assert isinstance(got[20], abc_.ReplyEvent) and got[20].message == "Done"
            assert isinstance(got[21], abc_.NoticeEvent) and got[21].kind is abc_.NoticeKind.COMMAND_FAILED
            assert all(not isinstance(e, abc_.InvalidEvent) and e.agent == "claude" for e in got)
    run(test, in_tmp)


def test_event_of_the_agent_that_breaks_the_schema_is_not_passed(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        session_id = rig.agent.add_session()
        async with rig.client() as bc:
            sc = bc.session("claude", session_id)
            items = sc.events()
            assert isinstance(await next_item(items), abc_.StateEvent)
            rig.agent.push_raw(session_id, b'event: status\ndata: {"kind": "other"}\n\n')
            rig.agent.push_raw(session_id, b'event: log\ndata: {"component": "a", "level": "INFO", '
                                           b'"time": "t", "message": "m"}\n\n')
            rig.agent.push(session_id, "status", {"kind": "activity", "text": "ok"})
            item = await next_item(items)
            assert isinstance(item, abc_.StatusEvent) and item.text == "ok"
    run(test, in_tmp)


def test_unknown_session_stream_is_gone(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as bc:
            sc = bc.session("claude", "no_session")
            with pytest.raises(abc_.SessionGoneError):
                await next_item(sc.events())
            sc2 = bc.session("nobody", "x")
            with pytest.raises(abc_.SessionGoneError):
                await next_item(sc2.events())
    run(test, in_tmp)


def test_two_clients_on_two_sessions_and_two_streams_on_one_session(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        one = rig.agent.add_session("client-1")
        two = rig.agent.add_session("client-2")
        async with rig.client("client-1") as bc1, rig.client("client-2") as bc2:
            a = bc1.session("claude", one)
            b = bc2.session("claude", two)
            c = bc2.session("claude", one)
            streams = [a.events(), b.events(), c.events()]
            for items in streams:
                assert isinstance(await next_item(items), abc_.StateEvent)
            for index in range(5):
                rig.agent.push(one, "status", {"kind": "activity", "text": f"one{index}"})
                rig.agent.push(two, "status", {"kind": "activity", "text": f"two{index}"})
            texts = []
            for items in streams:
                got = [await next_item(items) for _ in range(5)]
                texts.append([(e.session, e.text) for e in got if isinstance(e, abc_.StatusEvent)])
            assert texts[0] == texts[2] == [(one, f"one{i}") for i in range(5)]
            assert texts[1] == [(two, f"two{i}") for i in range(5)]
            assert rig.agent.open_streams(one) == 2 and rig.agent.open_streams(two) == 1
    run(test, in_tmp)


def test_upstream_end_closes_the_client_stream_and_the_client_connects_again(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        session_id = rig.agent.add_session()
        async with rig.client() as bc:
            sc = bc.session("claude", session_id)
            items = sc.events()
            assert isinstance(await next_item(items), abc_.StateEvent)
            rig.agent.close_streams(session_id)
            assert isinstance(await next_item(items), abc_.StateEvent)   # the new connection
            assert rig.agent.session_connects[session_id] == 2
            assert rig.broker.proxy.sessions.count("claude") == 1
    run(test, in_tmp)


def test_upstream_end_while_working_gives_command_failed(in_tmp: Path) -> None:
    """DESIGN.md, section 6.1.1, "Failures": the upstream session stream ends in the state working."""
    async def test(rig: Rig) -> None:
        session_id = rig.agent.add_session()
        async with rig.client() as bc:
            sc = bc.session("claude", session_id)
            items = sc.events()
            assert isinstance(await next_item(items), abc_.StateEvent)
            command_id = uuid.uuid4()
            await sc.send_command(command_id, "work")
            assert isinstance(await next_item(items), abc_.StateEvent)    # working
            rig.agent.close_streams(session_id)
            notice = await next_item(items)
            assert isinstance(notice, abc_.NoticeEvent)
            assert (notice.kind, notice.command_id) == ("command_failed", str(command_id))
    run(test, in_tmp)


def test_open_command_is_forgotten_after_its_reply_and_after_delete(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        session_id = rig.agent.add_session()
        async with rig.client() as bc:
            sc = bc.session("claude", session_id)
            items = sc.events()
            assert isinstance(await next_item(items), abc_.StateEvent)
            command_id = uuid.uuid4()
            await sc.send_command(command_id, "work")
            assert isinstance(await next_item(items), abc_.StateEvent)
            assert rig.broker.proxy.commands == {("claude", session_id): str(command_id)}
            rig.agent.push(session_id, "reply", {"session": session_id, "command_id": str(command_id),
                                                 "message": "done", "language": "en"})
            assert isinstance(await next_item(items), abc_.ReplyEvent)
            assert rig.broker.proxy.commands == {}
            rig.agent.set_state(session_id, "idle")
            await sc.send_command(uuid.uuid4(), "more")
            assert rig.broker.proxy.commands
            await bc.delete_session("claude", session_id)
            assert rig.broker.proxy.commands == {}
    run(test, in_tmp)


def test_client_that_goes_away_closes_the_upstream_stream(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        session_id = rig.agent.add_session()
        async with rig.client() as bc:
            sc = bc.session("claude", session_id)
            assert isinstance(await next_item(sc.events()), abc_.StateEvent)
            assert rig.agent.open_streams(session_id) == 1
            await sc.close()
            await until(lambda: rig.agent.open_streams(session_id) == 0 and rig.agent.session_ends[session_id] == 1)
            await until(lambda: rig.broker.proxy.sessions.count() == 0)
    run(test, in_tmp)


def test_heartbeat_lines_on_an_idle_stream(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        session_id = rig.agent.add_session()
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=rig.ssl_context())) as http:
            for path in (f"/agents/claude/sessions/{session_id}/events", "/events"):
                async with http.get(rig.url + path, headers={"Authorization": rig.auth_header()}) as response:
                    assert response.status == 200
                    data = b""
                    while data.count(b": keep-alive\n\n") < 2:
                        data += await asyncio.wait_for(response.content.readany(), 2)
                    if "sessions" in path:
                        assert data.startswith(b"event: state\n")
        # A client with a short heartbeat timeout keeps its stream.
        async with rig.client(heartbeat=0.5) as bc:
            sc = bc.session("claude", session_id)
            assert isinstance(await next_item(sc.events()), abc_.StateEvent)
            await asyncio.sleep(1.2)
            assert sc.connection_state is abc_.ConnectionState.CONNECTED
            assert rig.agent.session_connects[session_id] == 2
    run(test, in_tmp, heartbeat_interval=0.1)


@pytest.mark.parametrize("stream", ["session", "broker"])
def test_full_send_queue_closes_the_stream(in_tmp: Path, stream: str) -> None:
    """DESIGN.md, section 6.1.1: a client that reads too slowly loses its stream. The upstream stream closes too."""
    async def test(rig: Rig) -> None:
        session_id = rig.agent.add_session()
        path = f"/agents/claude/sessions/{session_id}/events" if stream == "session" else "/events"
        reader, writer = await asyncio.open_connection("127.0.0.1", rig.broker.port, ssl=rig.ssl_context(),
                                                       server_hostname="127.0.0.1", limit=1024)
        # The client sends the request and then reads nothing.
        writer.write(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: {rig.auth_header()}\r\n\r\n".encode())
        await writer.drain()
        big = "x" * 65536
        if stream == "session":
            await until(lambda: rig.agent.open_streams(session_id) == 1)
            for index in range(400):
                rig.agent.push(session_id, "status", {"kind": "activity", "text": f"{index}{big}"})
            await until(lambda: rig.agent.open_streams(session_id) == 0, timeout=10)
            await until(lambda: rig.broker.proxy.sessions.count() == 0)
        else:
            await until(lambda: rig.broker.proxy.hub.count == 1)
            for index in range(400):
                rig.agent.push_agent("log", {"component": "c", "level": "INFO", "time": "t", "message": big})
            await until(lambda: rig.broker.proxy.hub.count == 0, timeout=10)
            assert rig.agent.open_agent_streams == 1   # the agent stream is permanent
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass
        assert reader is not None
    run(test, in_tmp, queue_limit=5)


# --- Start and stop --------------------------------------------------------------------------

def test_stop_closes_each_stream_and_leaves_no_task(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        session_id = rig.agent.add_session()
        async with rig.client() as bc:
            sc = bc.session("claude", session_id)
            assert isinstance(await next_item(sc.events()), abc_.StateEvent)
            events = BrokerEvents(bc)
            await until(lambda: rig.broker.proxy.hub.count == 1)
            await rig.broker.stop()
            await until(lambda: rig.agent.open_streams(session_id) == 0 and rig.agent.open_agent_streams == 0)
            await until(lambda: rig.docker.open_event_streams == 0)
            assert rig.broker.proxy.hub.count == 0 and rig.broker.proxy.sessions.count() == 0
            await until(lambda: sc.connection_state is abc_.ConnectionState.RECONNECTING)
            await events.close()
    run(test, in_tmp)


def test_sigterm_stops_the_broker(in_tmp: Path) -> None:
    async def main() -> None:
        tls = make_tls(in_tmp / "tls")
        agent = FakeAgent()
        docker = FakeDocker("docker.sock")
        await agent.start()
        await docker.start()
        broker = Broker(settings_for(agent, tls), fake_secrets(tls), server_context(tls.cert, tls.key_pem, in_tmp))
        task = asyncio.create_task(broker.run())
        await until(lambda: broker.port is not None and agent.open_agent_streams == 1)
        os.kill(os.getpid(), signal.SIGTERM)
        assert await asyncio.wait_for(task, 5) == 0
        await until(lambda: agent.open_agent_streams == 0)
        await agent.stop()
        await docker.stop()
        assert not await pending_tasks()
    asyncio.run(main())



# --- Defects that a review found ---------------------------------------------------------------

class Lines(logging.Handler):
    """Collect the log lines of the broker."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(f"{record.levelname} {record.getMessage()}")


@pytest.fixture
def lines() -> Iterator[list[str]]:
    handler = Lines()
    logger = logging.getLogger("agent_broker")
    level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield handler.lines
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)


def test_dot_segments_of_a_url_do_not_change_the_path_at_the_agent(in_tmp: Path) -> None:
    """``..`` as a session ID or a key must not reach another operation of the agent."""
    async def test(rig: Rig) -> None:
        rig.agent.requests.clear()
        cases = [
            ("GET", "/agents/claude/sessions/%2e%2e/events", None),     # else: GET /events
            ("GET", "/agents/claude/sessions/%2e%2e", None),            # else: GET /
            ("DELETE", "/agents/claude/sessions/%2e", None),            # else: DELETE /sessions/
            ("POST", "/agents/claude/sessions/%2e%2e/interrupt", None),
            ("GET", "/agents/claude/config/%2e%2e", None),
            ("GET", "/agents/claude/config/%2e%2e%2fstatus", None),
        ]
        for method, path, data in cases:
            status, _ = await rig.raw(method, path, data=data)
            assert status == 404, (method, path)
        # Only the last case goes to the agent: its key is one segment with an encoded slash.
        assert rig.agent.requests == [("GET", "/config/..%2Fstatus")]
    run(test, in_tmp)


def test_redirect_of_the_agent_is_not_followed(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        async with rig.client() as bc:
            rig.agent.requests.clear()
            rig.agent.raw_next = (302, b"")
            rig.agent.raw_next_headers = {"Location": "/sessions"}
            with pytest.raises(abc_.AgentUnavailableError):
                await bc.get_config("claude")
            assert ("GET", "/sessions") not in rig.agent.requests
    run(test, in_tmp)


def test_head_opens_no_stream(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        session_id = rig.agent.add_session()
        for path in ("/events", f"/agents/claude/sessions/{session_id}/events"):
            status, _ = await rig.raw("HEAD", path)
            assert status == 404
            status, _ = await rig.raw("HEAD", path, authorization=None)
            assert status == 401
        assert rig.agent.session_connects[session_id] == 0
        assert rig.broker.proxy.hub.count == 0 and rig.broker.proxy.sessions.count() == 0
    run(test, in_tmp)


def test_body_that_is_too_large_or_too_deep_gives_400(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        session_id = rig.agent.add_session()
        path = f"/agents/claude/sessions/{session_id}/commands"
        for data in (json.dumps({"id": str(uuid.uuid4()), "text": "x" * 2_000_000, "mode": "text"}), "[" * 200_000):
            status, text = await rig.raw("POST", path, data=data)
            assert (status, json.loads(text)["error"]) == (400, "bad_request")
        assert not [r for r in rig.agent.requests if r[0] == "POST"]
    run(test, in_tmp)


def test_dropped_event_gives_a_log_line_without_the_payload(in_tmp: Path, lines: list[str]) -> None:
    """The broker drops no event silently. The line names the agent, the session and the reason, not the content."""
    async def test(rig: Rig) -> None:
        session_id = rig.agent.add_session()
        async with rig.client() as bc:
            sc = bc.session("claude", session_id)
            items = sc.events()
            assert isinstance(await next_item(items), abc_.StateEvent)
            rig.agent.push_raw(session_id, b'event: status\ndata: {"session": "' + session_id.encode()
                               + b'", "kind": "activity", "text": {"private-user-text": 1}}\n\n')
            rig.agent.push_raw(session_id, b'event: reply\ndata: ["private-user-text"]\n\n')
            rig.agent.push_raw(session_id, b'event: status\ndata: private-user-text\n\n')
            rig.agent.push(session_id, "status", {"kind": "activity", "text": "ok"})
            item = await next_item(items)
            assert isinstance(item, abc_.StatusEvent) and item.text == "ok"
        dropped = [line for line in lines if "not passed" in line]
        assert len(dropped) == 3
        assert all(line.startswith("WARNING") and "claude" in line and session_id in line for line in dropped)
        assert "text" in dropped[0] and "type" in dropped[0]
        assert not [line for line in lines if "private-user-text" in line]
    run(test, in_tmp)


def test_event_with_too_many_levels_is_dropped_and_the_stream_continues(in_tmp: Path) -> None:
    async def test(rig: Rig) -> None:
        session_id = rig.agent.add_session()
        async with rig.client() as bc:
            sc = bc.session("claude", session_id)
            items = sc.events()
            assert isinstance(await next_item(items), abc_.StateEvent)
            rig.agent.push_raw(session_id, b"event: status\ndata: " + b"[" * 200_000 + b"\n\n")
            rig.agent.push(session_id, "status", {"kind": "activity", "text": "ok"})
            item = await next_item(items)
            assert isinstance(item, abc_.StatusEvent) and item.text == "ok"
            assert rig.agent.session_connects[session_id] == 1
    run(test, in_tmp)


def test_log_line_of_a_failed_login_has_no_line_break(in_tmp: Path, lines: list[str]) -> None:
    async def test(rig: Rig) -> None:
        status, _ = await rig.raw("GET", "/x%0AINFO%20forged", authorization=None)
        assert status == 401
    run(test, in_tmp)
    failed = [line for line in lines if "Authentication failed" in line]
    assert len(failed) == 1 and "\n" not in failed[0] and "%0A" in failed[0]


def test_failure_of_the_pump_ends_the_client_stream(in_tmp: Path, lines: list[str]) -> None:
    """A failure of the pump must not leave the client stream open with a dead upstream stream."""
    class Broken(UpstreamStream):
        async def events(self) -> AsyncIterator[Event]:
            raise RuntimeError("broken")
            yield "state", {}   # makes an asynchronous generator

        async def close(self) -> None:
            return None

    async def test(rig: Rig) -> None:
        stream = ClientStream(make_mocked_request("GET", "/events"), web.StreamResponse(), label="s", limit=2,
                              heartbeat=1)
        await rig.broker.proxy._pump("claude", "ses_x", Broken(), stream)
        assert stream.waiting == 1   # the end mark
    run(test, in_tmp)
    assert [line for line in lines if "the upstream stream failed" in line]


def test_full_queue_writes_the_number_of_lost_events(lines: list[str]) -> None:
    """DESIGN.md, section 6.1.1: the broker writes the number of lost events of a closed stream to its log."""
    async def test() -> None:
        stream = ClientStream(make_mocked_request("GET", "/events"), web.StreamResponse(), label="s", limit=2,
                              heartbeat=1)
        body: JsonObject = {"agent": "claude", "session": "ses_1", "state": "idle"}
        assert stream.offer("state", body) and stream.offer("state", body)
        assert not stream.offer("state", body)
        assert stream.closed and stream.overflow and stream.waiting == 0
    asyncio.run(test())
    assert [line for line in lines if "3 events that the client did not get" in line]


class GoneResponse(web.StreamResponse):
    """A response of a client that went away: each write fails."""

    async def write(self, data: bytes | bytearray | memoryview) -> None:
        raise ConnectionResetError("the client went away")


def test_client_that_goes_away_writes_the_number_of_lost_events(lines: list[str]) -> None:
    async def test() -> None:
        stream = ClientStream(make_mocked_request("GET", "/events"), GoneResponse(), label="s", limit=5, heartbeat=1)
        for _ in range(3):
            assert stream.offer("state", {"agent": "claude", "session": "ses_1", "state": "idle"})
        await asyncio.wait_for(stream.run(), 5)
        assert stream.closed and not stream.offer("state", {"agent": "claude", "session": "ses_1", "state": "idle"})
    asyncio.run(test())
    # The write of the first event failed. Two events waited.
    assert [line for line in lines if "2 events that the client did not get" in line]
