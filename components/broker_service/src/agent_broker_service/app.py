"""Start and stop the broker service.

``main`` reads the configuration, opens the secrets from the vault, makes the TLS context
and runs the broker until SIGTERM or SIGINT. The broker keeps no state on disk.
"""
from __future__ import annotations

import asyncio
import signal
import ssl

from aiohttp import web

from agent_broker_client import config as cfg
from agent_broker_client import dependencies, secrets_proxy

from . import logs, router, settings, tls
from .api import Api
from .docker import DockerEngine
from .proxy import EventProxy
from .settings import Settings
from .version import version

log = logs.get("broker")
# The wait of the aiohttp runner for open requests at the stop, in seconds.
SHUTDOWN_TIMEOUT_S = 2.0


class Broker:
    """The broker service: the API, the router, the event proxy and the container control.

    ``secrets`` gives ``api_username`` and ``api_password``. ``ssl_context`` holds the certificate and the key.
    """

    def __init__(self, conf: Settings, secrets: secrets_proxy.Secrets, ssl_context: ssl.SSLContext):
        self.settings = conf
        self.ssl_context = ssl_context
        self.docker = DockerEngine(conf.docker_socket, inspect_timeout=conf.docker_inspect_timeout,
                                   control_timeout=conf.docker_control_timeout)
        opencode = secrets.get_secret("opencode_password") if "opencode_password" in settings.start_secrets(conf) else None
        self.router = router.build(conf, router.factories(opencode, self.docker))
        self.proxy = EventProxy(self.router, self.docker, heartbeat=conf.heartbeat_interval,
                                queue_limit=conf.queue_limit, reconnect_interval=conf.reconnect_interval)
        self.api = Api(self.router, self.docker, self.proxy, secrets.get_secret("api_username"), secrets.get_secret("api_password"))
        self._runner: web.AppRunner | None = None
        self.port: int | None = None

    async def start(self, host: str | None = None, port: int | None = None) -> int:
        """Open the providers, the upstream streams and the HTTPS port. Give the port."""
        for entry in self.router.entries:
            await entry.provider.open()
        app = self.api.app()
        # handler_cancellation: a client that goes away cancels its stream at once.
        runner = web.AppRunner(app, handle_signals=False, handler_cancellation=True,
                               shutdown_timeout=SHUTDOWN_TIMEOUT_S, access_log=None)
        await runner.setup()
        self._runner = runner
        host = host if host is not None else self.settings.host
        site = web.TCPSite(runner, host, port if port is not None else self.settings.port,
                           ssl_context=self.ssl_context)
        await site.start()
        address = runner.addresses[0]
        self.port = int(address[1])
        self.proxy.start()
        log.info("Listening on %s:%s with the agents: %s", host, self.port,
                 ", ".join(e.name for e in self.router.entries) or "none")
        return self.port

    async def stop(self) -> None:
        """Close each stream, then the port and each connection to an agent and to Docker."""
        log.info("The broker stops")
        self.api.closing = True
        await self.proxy.close()
        runner, self._runner = self._runner, None
        if runner is not None:
            await runner.cleanup()
        for entry in self.router.entries:
            await entry.provider.close()
        await self.docker.close()

    async def run(self) -> int:
        """Run until SIGTERM or SIGINT. Give the exit code."""
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        signals = (signal.SIGTERM, signal.SIGINT)
        for number in signals:
            loop.add_signal_handler(number, stop.set)
        try:
            await self.start()
            await stop.wait()
        finally:
            for number in signals:
                loop.remove_signal_handler(number)
            await self.stop()
        return 0


def main() -> int:
    logs.setup()
    log.info("Agent Broker starts, version %s", version(settings.PROJECT))
    try:
        conf = settings.load()
        values = settings.from_config(conf)
        logs.setup(values.log_level)
        credentials = conf.section("credentials")
        refs = credentials.get("secrets")
        wanted = settings.start_secrets(values)
        start = {name: ref for name, ref in refs.items() if name in wanted} if isinstance(refs, dict) else refs
        secrets = secrets_proxy.open_secrets({**credentials, "secrets": start},
                                             lambda name: dependencies.find(name, settings.PROJECT))
        context = tls.server_context(values.cert_file, secrets.get_secret("tls_private_key"), settings.TLS_SCRATCH)
        broker = Broker(values, secrets, context)
    except (cfg.ConfigError, secrets_proxy.SecretError, dependencies.DependencyError, OSError, ssl.SSLError) as exc:
        log.error("Start failed: %s", exc)
        return 1
    try:
        code = asyncio.run(broker.run())
    except Exception:  # noqa: BLE001 - write the cause to the log
        log.exception("The broker stopped with an error")
        code = 1
    log.info("Agent Broker ends with exit code %s", code)
    return code
