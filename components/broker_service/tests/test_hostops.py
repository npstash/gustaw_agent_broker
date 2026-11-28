"""The container control through hostops: the engine against a fake hostops, and the broker with it."""
from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable, Coroutine
from pathlib import Path

import pytest
from fake_agent import FakeAgent
from fake_hostops import FakeHostops
from rig import CONTAINER, fake_secrets, make_tls, pending_tasks, settings_for, until

import agent_broker_client as abc_
from agent_broker_client.models import ContainerState
from agent_broker_service.app import Broker, container_engine
from agent_broker_service.docker import ContainerEvent, DockerEngine
from agent_broker_service.errors import ContainerMissingError, DockerUnavailableError
from agent_broker_service.hostops import HostopsEngine, HostopsError
from agent_broker_service.settings import AgentSettings, Settings
from agent_broker_service.tls import server_context

SOCKET = "hostops.sock"
type Test = Callable[[FakeHostops, HostopsEngine], Coroutine[object, object, None]]


@pytest.fixture
def in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run the test in tmp_path. The socket path is relative to it."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run(test: Test) -> None:
    async def main() -> None:
        fake = FakeHostops(SOCKET)
        await fake.start()
        engine = HostopsEngine(SOCKET, inspect_timeout=2.0, control_timeout=2.0)
        try:
            await asyncio.wait_for(test(fake, engine), 10)
        finally:
            await engine.close()
            await fake.stop()
        assert not await pending_tasks()
    asyncio.run(main())


def test_inspect_lists_the_containers_and_takes_the_exact_name(in_tmp: Path) -> None:
    async def test(fake: FakeHostops, engine: HostopsEngine) -> None:
        fake.containers["claude_code_server"] = "paused"
        assert (await engine.inspect("claude_code_server")).state is ContainerState.PAUSED
        assert (await engine.inspect("claude_code_server_old")).state is ContainerState.EXITED
        assert (await engine.inspect("claude_code")).state is ContainerState.MISSING     # a prefix is not a name
        fake.containers["opencode"] = "removing"
        assert (await engine.inspect("opencode")).state is ContainerState.EXITED
        assert fake.requests == [{"op": "list-containers"}] * 4

    run(test)


def test_start_stop_and_restart_send_control_container(in_tmp: Path) -> None:
    async def test(fake: FakeHostops, engine: HostopsEngine) -> None:
        await engine.stop("opencode")
        assert fake.containers["opencode"] == "exited"
        await engine.start("opencode")
        await engine.restart("claude_code_server")
        assert fake.requests == [
            {"op": "control-container", "container": "opencode", "action": "stop"},
            {"op": "control-container", "container": "opencode", "action": "start"},
            {"op": "control-container", "container": "claude_code_server", "action": "restart"}]

    run(test)


def test_failed_control_of_a_missing_container_is_container_missing(in_tmp: Path) -> None:
    async def test(fake: FakeHostops, engine: HostopsEngine) -> None:
        fake.control.add("gone")
        with pytest.raises(ContainerMissingError):
            await engine.restart("gone")
        assert fake.requests[-1] == {"op": "list-containers"}      # the check after "failed"

    run(test)


def test_refusals_are_docker_unavailable(in_tmp: Path) -> None:
    async def test(fake: FakeHostops, engine: HostopsEngine) -> None:
        fake.control.discard("opencode")
        with pytest.raises(HostopsError, match="not-granted") as info:
            await engine.restart("opencode")
        assert isinstance(info.value, DockerUnavailableError) and info.value.code == "not-granted"
        fake.list_granted = False
        with pytest.raises(DockerUnavailableError, match="not-granted"):
            await engine.inspect("opencode")
        fake.raw_answer = b"not json\n"
        with pytest.raises(DockerUnavailableError, match="not JSON"):
            await engine.inspect("opencode")
        fake.raw_answer = b'{"ok": true, "result": []}'           # no line end: the connection closes first
        with pytest.raises(DockerUnavailableError, match="no complete answer"):
            await engine.inspect("opencode")

    run(test)


def test_missing_socket_is_docker_unavailable(in_tmp: Path) -> None:
    async def main() -> None:
        engine = HostopsEngine("no-such.sock", inspect_timeout=1.0, control_timeout=1.0)
        with pytest.raises(DockerUnavailableError, match="not reachable"):
            await engine.inspect("opencode")
        with pytest.raises(DockerUnavailableError, match="not reachable"):
            async for _ in engine.events(["opencode"]):
                pass
    asyncio.run(main())


def test_events_merge_one_watch_stream_for_each_container(in_tmp: Path) -> None:
    async def test(fake: FakeHostops, engine: HostopsEngine) -> None:
        received: list[ContainerEvent] = []

        async def read() -> None:
            async for event in engine.events(["opencode", "claude_code_server", "opencode"]):
                received.append(event)

        reader = asyncio.create_task(read())
        await until(lambda: fake.open_watches("opencode") == 1 and fake.open_watches("claude_code_server") == 1)
        assert sorted(str(r["containers"]) for r in fake.requests) == ["['claude_code_server']", "['opencode']"]
        assert all(set(r) == {"op", "containers"} and r["op"] == "watch-containers" for r in fake.requests)
        fake.emit("opencode", "exec_start", execID="e1")             # not a lifecycle event
        fake.emit("opencode", "die", "137")
        fake.emit("claude_code_server", "start")
        await until(lambda: len(received) == 2)
        assert ContainerEvent("opencode", "die", "137") in received
        assert ContainerEvent("claude_code_server", "start", None) in received
        reader.cancel()
        await asyncio.wait([reader])
        await until(lambda: fake.open_watches("opencode") == 0 and fake.open_watches("claude_code_server") == 0)

    run(test)


def test_refused_watch_ends_the_events_with_an_error_and_closes_the_other_streams(in_tmp: Path) -> None:
    async def test(fake: FakeHostops, engine: HostopsEngine) -> None:
        fake.watch.discard("opencode")
        with pytest.raises(HostopsError, match="not-granted"):
            async for _ in engine.events(["opencode", "claude_code_server"]):
                pass
        await until(lambda: fake.open_watches("claude_code_server") == 0)

    run(test)


def test_end_of_a_watch_stream_ends_the_events(in_tmp: Path) -> None:
    async def test(fake: FakeHostops, engine: HostopsEngine) -> None:
        async def read() -> list[ContainerEvent]:
            return [event async for event in engine.events(["opencode"])]

        reader = asyncio.create_task(read())
        await until(lambda: fake.open_watches("opencode") == 1)
        for writer in list(fake._watchers["opencode"]):
            writer.close()
        assert await asyncio.wait_for(reader, 5) == []

    run(test)


def test_settings_select_the_engine() -> None:
    agents = (AgentSettings("claude", "agent_api", "http://127.0.0.1:1", CONTAINER),)
    assert isinstance(container_engine(Settings(agents=agents)), DockerEngine)
    engine = container_engine(Settings(agents=agents, hostops_socket="/run/gustaw/hostops.sock"))
    assert isinstance(engine, HostopsEngine) and engine.socket_path == "/run/gustaw/hostops.sock"


def test_broker_controls_the_agent_through_hostops(in_tmp: Path) -> None:
    async def main() -> None:
        tls = make_tls(in_tmp / "tls")
        agent = FakeAgent()
        hostops = FakeHostops(SOCKET)
        await agent.start()
        await hostops.start()
        conf = dataclasses.replace(settings_for(agent, tls), hostops_socket=SOCKET, docker_socket="no-docker.sock")
        broker = Broker(conf, fake_secrets(tls), server_context(tls.cert, tls.key_pem, in_tmp / "scratch"))
        await broker.start()
        try:
            await until(lambda: hostops.open_watches(CONTAINER) == 1 and agent.open_agent_streams == 1)
            client = abc_.HttpBrokerClient(f"https://127.0.0.1:{broker.port}", "broker-user", "broker-test-password",
                                           "client-1", cert_file=tls.cert, heartbeat_timeout=30, reconnect_interval=0.1,
                                           request_timeout=5)
            async with client as bc:
                [info] = await bc.list_agents()
                assert info.container_state is abc_.ContainerState.RUNNING
                notices: list[str] = []

                async def read() -> None:
                    async for item in bc.events():
                        if isinstance(item, abc_.NoticeEvent):
                            notices.append(item.text)

                reader = asyncio.create_task(read())
                await until(lambda: broker.proxy.hub.count == 1)
                result = await bc.restart_agent("claude")
                assert result.container_state is abc_.ContainerState.RUNNING
                await until(lambda: "container restarted" in notices)
                assert notices == ["container ended, exit code 0", "container started", "container restarted"]
                reader.cancel()
                await asyncio.wait([reader])
            assert {"op": "control-container", "container": CONTAINER, "action": "restart"} in hostops.requests
        finally:
            await broker.stop()
            await agent.stop()
            await hostops.stop()
        assert not await pending_tasks()
    asyncio.run(main())
