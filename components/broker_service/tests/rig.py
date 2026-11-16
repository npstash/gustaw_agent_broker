"""The test rig: the real broker with TLS, a fake agent, a fake Docker engine and fake secrets.

The tests drive the broker with the real client library of the component client. Each test
runs its own event loop. The intervals are short, so no test sleeps long.
"""
from __future__ import annotations

import asyncio
import base64
import ssl
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path

import aiohttp
from fake_agent import FakeAgent
from fake_docker import FakeDocker
from yarl import URL

import agent_broker_client as abc_
from agent_broker_client import certtool
from agent_broker_client.vault import FakeSecrets
from agent_broker_service.app import Broker
from agent_broker_service.settings import AgentSettings, Settings
from agent_broker_service.tls import server_context

USERNAME = "broker-user"
PASSWORD = "broker-test-password"
CONTAINER = "claude_code_server"
NO_TIMEOUT = 30.0    # the heartbeat timeout of the clients: a slow machine causes no reconnect
RECONNECT = 0.1
TEST_TIMEOUT = 20.0


@dataclass
class TlsFiles:
    cert: Path
    key_pem: str


def make_tls(folder: Path) -> TlsFiles:
    """A key pair from certtool. The key is a test key, not a secret."""
    cert_pem, key_pem = certtool.make_key_pair(["localhost", "127.0.0.1"], 2)
    folder.mkdir(parents=True, exist_ok=True)
    cert = folder / "server.crt"
    cert.write_bytes(cert_pem)
    return TlsFiles(cert, key_pem)


def fake_secrets(tls: TlsFiles) -> FakeSecrets:
    return FakeSecrets({"api_username": USERNAME, "api_password": PASSWORD, "tls_private_key": tls.key_pem})


def settings_for(agent: FakeAgent, tls: TlsFiles, *, heartbeat_interval: float = NO_TIMEOUT,
                 queue_limit: int = 100) -> Settings:
    return Settings(
        # The agent opencode is disabled, like in the default configuration: the broker does not serve it.
        agents=(AgentSettings("claude", "agent_api", agent.url, CONTAINER),
                AgentSettings("opencode", "opencode", "http://127.0.0.1:1", "opencode", enabled=False)),
        host="127.0.0.1", port=0, heartbeat_interval=heartbeat_interval, queue_limit=queue_limit,
        docker_socket="docker.sock", cert_file=tls.cert, reconnect_interval=RECONNECT, agent_request_timeout=2.0,
        docker_inspect_timeout=2.0, docker_control_timeout=2.0)


@dataclass
class Rig:
    broker: Broker
    agent: FakeAgent
    docker: FakeDocker
    tls: TlsFiles
    scratch: Path

    @property
    def url(self) -> str:
        return f"https://127.0.0.1:{self.broker.port}"

    def client(self, client_id: str = "client-1", password: str = PASSWORD,
               heartbeat: float = NO_TIMEOUT) -> abc_.HttpBrokerClient:
        return abc_.HttpBrokerClient(self.url, USERNAME, password, client_id, cert_file=self.tls.cert,
                                     heartbeat_timeout=heartbeat, reconnect_interval=RECONNECT, request_timeout=5)

    def ssl_context(self) -> ssl.SSLContext:
        return abc_.client_context(self.tls.cert)

    def auth_header(self, password: str = PASSWORD) -> str:
        return "Basic " + base64.b64encode(f"{USERNAME}:{password}".encode()).decode("ascii")

    async def raw(self, method: str, path: str, *, data: str | None = None,
                  authorization: str | None = "") -> tuple[int, str]:
        """One request without the client library. ``authorization=""`` sends the correct login."""
        headers = {"Authorization": self.auth_header() if authorization == "" else authorization} \
            if authorization is not None else {}
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=self.ssl_context())) as http:
            # encoded: the path goes to the broker as it is, e.g. with ``%2e%2e``.
            async with http.request(method, URL(self.url + path, encoded=True), data=data,
                                    headers=headers) as response:
                return response.status, await response.text()


def run(test: Callable[[Rig], Coroutine[object, object, None]], tmp_path: Path, *,
        heartbeat_interval: float = NO_TIMEOUT, queue_limit: int = 100) -> None:
    """Start the fakes and the broker, run the test, stop all. Check that no task stays.

    The caller must run in ``tmp_path`` (fixture ``in_tmp``): the Docker socket path is relative.
    """
    async def main() -> None:
        tls = make_tls(tmp_path / "tls")
        agent = FakeAgent()
        docker = FakeDocker("docker.sock")
        await agent.start()
        await docker.start()
        context = server_context(tls.cert, tls.key_pem, tmp_path / "scratch")
        broker = Broker(settings_for(agent, tls, heartbeat_interval=heartbeat_interval, queue_limit=queue_limit), fake_secrets(tls), context)
        await broker.start()
        rig = Rig(broker, agent, docker, tls, tmp_path / "scratch")
        try:
            await until(lambda: agent.open_agent_streams == 1 and docker.open_event_streams == 1)
            await asyncio.wait_for(test(rig), TEST_TIMEOUT)
        finally:
            await broker.stop()
            await agent.stop()
            await docker.stop()
        assert not await pending_tasks()
    asyncio.run(main())


async def pending_tasks() -> list[asyncio.Task[object]]:
    """The tasks that are not done, other than the current one. Wait at most 1 s for them to end.

    A failed TLS handshake can leave an accept task of a server. That task is not a task of the broker.
    """
    current = asyncio.current_task()
    end = time.monotonic() + 1
    while True:
        pending = [t for t in asyncio.all_tasks()
                   if t is not current and not t.done() and "_accept_connection" not in repr(t)]
        if not pending or time.monotonic() > end:
            return pending
        await asyncio.sleep(0.01)


async def until(check: Callable[[], bool], timeout: float = 5) -> None:
    end = time.monotonic() + timeout
    while not check():
        if time.monotonic() > end:
            raise AssertionError("the condition did not become true")
        await asyncio.sleep(0.01)


class BrokerEvents:
    """Read the broker stream of a broker client in a task. The broker stream connects at the first read."""

    def __init__(self, client: abc_.HttpBrokerClient):
        self.queue: asyncio.Queue[abc_.BrokerEvent | abc_.InvalidEvent] = asyncio.Queue()
        self.task = asyncio.create_task(self._read(client))

    async def _read(self, client: abc_.HttpBrokerClient) -> None:
        async for item in client.events():
            self.queue.put_nowait(item)

    async def next(self, timeout: float = 5) -> abc_.BrokerEvent | abc_.InvalidEvent:
        return await asyncio.wait_for(self.queue.get(), timeout)

    async def close(self) -> None:
        self.task.cancel()
        await asyncio.wait([self.task])


async def next_item[T](items: AsyncIterator[T], timeout: float = 5) -> T:
    return await asyncio.wait_for(anext(items), timeout)
