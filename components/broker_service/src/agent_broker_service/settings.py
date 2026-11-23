"""The configuration of the broker service.

``config/config.yaml`` holds the default values. The optional local file ``local.yaml`` of the configuration
folder ``${XDG_CONFIG_HOME:-$HOME/.config}/gustaw/agent_broker/`` replaces values of the same keys. A local file cannot add a key,
so it cannot add an agent.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from agent_broker_client import RECONNECT_INTERVAL_S
from agent_broker_client import config as cfg
from agent_broker_client.jsontypes import JsonValue
from agent_broker_client.vault import resolve_path

PROJECT = Path(__file__).resolve().parents[2]
CONFIG_FILE = PROJECT / "config" / "config.yaml"
# The folder of the service below each XDG base folder.
APP_FOLDER = Path("gustaw") / "agent_broker"


def xdg_folder(variable: str, default: str) -> Path:
    """The folder of the service below the XDG base folder of ``variable``, e.g. XDG_CONFIG_HOME."""
    base = os.environ.get(variable) or str(Path.home() / default)
    return Path(base) / APP_FOLDER


LOCAL_CONFIG = xdg_folder("XDG_CONFIG_HOME", ".config") / "local.yaml"
# The private key goes into a file here for some milliseconds only (see tls.server_context).
# The XDG runtime folder, else the cache folder: in the container, both are in the tmpfs HOME.
TLS_SCRATCH = xdg_folder("XDG_RUNTIME_DIR", ".cache") / "tls"

PROVIDERS = ("agent_api", "opencode")
SECRET_NAMES = ("tls_private_key", "api_username", "api_password", "opencode_password")
# The secrets that the service opens at start. The password of OpenCode only if an agent with the provider opencode
# is enabled (start_secrets).
START_SECRETS = ["tls_private_key", "api_username", "api_password"]


def start_secrets(conf: Settings) -> list[str]:
    names = list(START_SECRETS)
    if any(agent.enabled and agent.provider == "opencode" for agent in conf.agents):
        names.append("opencode_password")
    return names

# The timeouts of the broker:
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
        if not isinstance(agent.get("enabled"), bool):
            raise cfg.ConfigError(f"the agent {name} needs enabled: true or false")
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
    "log.level": cfg.log_level,
    "streams.heartbeat_interval_s": cfg.number_range(0.001),
    "streams.heartbeat_timeout_s": cfg.number_range(0.001),
    "streams.queue_limit": cfg.number_range(1),
    "docker.socket": _text,
    "language.threshold": cfg.number_range(0.0, 1.0),
    "cert.file": _text,
    "credentials.vault": lambda value: None,
    **{f"credentials.secrets.{name}": cfg.secret_ref for name in SECRET_NAMES},
}


@dataclass(frozen=True)
class AgentSettings:
    """One agent of the configuration (``agents.<name>``)."""
    name: str
    provider: str
    url: str
    container: str
    enabled: bool = True
    workspace_root: str = ""     # the provider opencode: the folder of the workspaces in its container


@dataclass(frozen=True)
class Settings:
    """The values that the service uses. The defaults are the defaults of the service."""
    agents: tuple[AgentSettings, ...]
    host: str = "0.0.0.0"
    port: int = 38443
    heartbeat_interval: float = 15.0
    queue_limit: int = 100
    docker_socket: str = "/var/run/docker.sock"
    log_level: str = "INFO"
    cert_file: Path = field(default_factory=lambda: resolve_path(PROJECT, "../client/tls/server.crt"))
    reconnect_interval: float = RECONNECT_INTERVAL_S
    agent_request_timeout: float = AGENT_REQUEST_TIMEOUT_S
    docker_inspect_timeout: float = DOCKER_INSPECT_TIMEOUT_S
    docker_control_timeout: float = DOCKER_CONTROL_TIMEOUT_S
    languages: tuple[str, ...] = ("en", "pl")
    language_threshold: float = 0.70
    reply_replay: float = 600.0


def load(path: Path = CONFIG_FILE, local_path: Path | None = LOCAL_CONFIG) -> cfg.ConfigFile:
    """Load the default values and the local values of this machine. Raise ConfigError for a wrong value."""
    return cfg.ConfigFile(path, VALIDATORS, local_path=local_path)


def from_config(conf: cfg.ConfigFile) -> Settings:
    """The settings of a checked configuration."""
    agents: list[AgentSettings] = []
    for name in conf.section("agents"):
        prefix = f"agents.{name}."
        root = conf.get(prefix + "workspace_root", "")
        agents.append(AgentSettings(name, conf.get_str(prefix + "provider"), conf.get_str(prefix + "url"),
                                    conf.get_str(prefix + "container"), enabled=conf.get_bool(prefix + "enabled"),
                                    workspace_root=root if isinstance(root, str) else ""))
    return Settings(
        agents=tuple(agents),
        host=conf.get_str("server.host"),
        port=conf.get_int("server.port"),
        heartbeat_interval=conf.get_float("streams.heartbeat_interval_s"),
        queue_limit=conf.get_int("streams.queue_limit"),
        docker_socket=conf.get_str("docker.socket"),
        log_level=conf.get_str("log.level").upper(),
        cert_file=resolve_path(PROJECT, conf.get_str("cert.file")),
        languages=tuple(str(v) for v in conf.get_list("language.languages")),
        language_threshold=conf.get_float("language.threshold"),
    )
