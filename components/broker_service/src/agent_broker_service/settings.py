"""The configuration of the broker service (DESIGN.md, sections 8.3 and 12).

``config/config.yaml`` holds the default values. The optional local file
``.local_assets/config/local.yaml`` replaces values of the same keys. A local file cannot add a key.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_broker_client import RECONNECT_INTERVAL_S
from agent_broker_client import config as cfg
from agent_broker_client.jsontypes import JsonValue
from agent_broker_client.vault import resolve_path

PROJECT = Path(__file__).resolve().parents[2]
CONFIG_FILE = PROJECT / "config" / "config.yaml"
LOCAL_CONFIG = PROJECT / ".local_assets" / "config" / "local.yaml"
# The private key goes into a file here for some milliseconds only (see tls.server_context).
TLS_SCRATCH = PROJECT / ".local_assets" / "tls"

PROVIDERS = ("agent_api", "opencode")
SECRET_NAMES = ("tls_private_key", "api_username", "api_password", "opencode_password")
# The secrets that the service opens at start. The provider opencode is not implemented, so its password is not opened.
START_SECRETS = ["tls_private_key", "api_username", "api_password"]

# Values that the DESIGN does not give. Choices of the service:
# - The broker waits AGENT_REQUEST_TIMEOUT for an answer of an agent, less than the request timeout of the client (15 s).
AGENT_REQUEST_TIMEOUT_S = 10.0
# - The broker waits DOCKER_INSPECT_TIMEOUT for an inspect of a container.
DOCKER_INSPECT_TIMEOUT_S = 10.0
# - The broker waits DOCKER_CONTROL_TIMEOUT for start, stop and restart. It is less than AGENT_CONTROL_TIMEOUT (60 s)
#   of the client, so that the client gets the answer of the broker.
DOCKER_CONTROL_TIMEOUT_S = 55.0


def _text(value: JsonValue) -> None:
    if not isinstance(value, str) or not value:
        raise cfg.ConfigError("the value must be a text that is not empty")


def _agents(value: JsonValue) -> None:
    if not isinstance(value, dict) or not value:
        raise cfg.ConfigError("give one or more agents")
    for name, agent in value.items():
        if not isinstance(agent, dict):
            raise cfg.ConfigError(f"the agent {name} must be a mapping")
        for key in ("provider", "url", "container"):
            if not isinstance(agent.get(key), str) or not agent.get(key):
                raise cfg.ConfigError(f"the agent {name} needs the text {key}")
        if agent["provider"] not in PROVIDERS:
            raise cfg.ConfigError(f"the agent {name} has the unknown provider {agent['provider']}; "
                                  f"give one of: {', '.join(PROVIDERS)}")


VALIDATORS: dict[str, cfg.Validator] = {
    "server.host": _text,
    "server.port": cfg.number_range(0, 65535),
    "agents": _agents,
    "streams.heartbeat_interval_s": cfg.number_range(0.001),
    "streams.heartbeat_timeout_s": cfg.number_range(0.001),
    "streams.queue_limit": cfg.number_range(1),
    "docker.socket": _text,
    "language.threshold": cfg.number_range(0.0, 1.0),
    "cert.file": _text,
    "credentials.vault": _text,
    **{f"credentials.secrets.{name}": cfg.secret_ref for name in SECRET_NAMES},
}


@dataclass(frozen=True)
class AgentSettings:
    """One agent of the configuration (``agents.<name>``)."""
    name: str
    provider: str
    url: str
    container: str


@dataclass(frozen=True)
class Settings:
    """The values that the service uses. The defaults are the defaults of DESIGN.md, section 12."""
    agents: tuple[AgentSettings, ...]
    host: str = "0.0.0.0"
    port: int = 38443
    heartbeat_interval: float = 15.0
    queue_limit: int = 100
    docker_socket: str = "/var/run/docker.sock"
    cert_file: Path = field(default_factory=lambda: resolve_path(PROJECT, "../client/tls/server.crt"))
    reconnect_interval: float = RECONNECT_INTERVAL_S
    agent_request_timeout: float = AGENT_REQUEST_TIMEOUT_S
    docker_inspect_timeout: float = DOCKER_INSPECT_TIMEOUT_S
    docker_control_timeout: float = DOCKER_CONTROL_TIMEOUT_S


def load(path: Path = CONFIG_FILE, local_path: Path | None = LOCAL_CONFIG) -> cfg.ConfigFile:
    """Load the default values and the local values of this machine. Raise ConfigError for a wrong value."""
    return cfg.ConfigFile(path, VALIDATORS, local_path=local_path)


def from_config(conf: cfg.ConfigFile) -> Settings:
    """The settings of a checked configuration."""
    agents: list[AgentSettings] = []
    for name, value in conf.section("agents").items():
        agent = cfg.as_str_map(f"agents.{name}", value)
        agents.append(AgentSettings(name, agent["provider"], agent["url"], agent["container"]))
    return Settings(
        agents=tuple(agents),
        host=conf.get_str("server.host"),
        port=conf.get_int("server.port"),
        heartbeat_interval=conf.get_float("streams.heartbeat_interval_s"),
        queue_limit=conf.get_int("streams.queue_limit"),
        docker_socket=conf.get_str("docker.socket"),
        cert_file=resolve_path(PROJECT, conf.get_str("cert.file")),
    )
