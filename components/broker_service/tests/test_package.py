"""The skeleton of the service: the package imports and the default configuration has all keys."""
from pathlib import Path

import agent_broker_service
from agent_broker_client import config as cfg

CONFIG_FILE = Path(__file__).resolve().parent.parent / "config" / "config.yaml"


def test_package_imports() -> None:
    assert agent_broker_service.__doc__


def test_default_configuration_follows_the_design() -> None:
    conf = cfg.ConfigFile(CONFIG_FILE)
    assert conf.get_str("server.host") == "0.0.0.0"
    assert conf.get_int("server.port") == 38443
    assert conf.get_int("streams.heartbeat_interval_s") == 15
    assert conf.get_int("streams.heartbeat_timeout_s") == 45
    assert conf.get_int("streams.queue_limit") == 100
    assert conf.get_str("docker.socket") == "/var/run/docker.sock"
    assert conf.get_list("language.languages") == ["en", "pl"]
    assert conf.get_float("language.threshold") == 0.70
    assert conf.get_str("cert.file") == "../client/tls/server.crt"
    assert conf.get_str_map("credentials.secrets.tls_private_key") == {
        "entry": "Agent Broker TLS 01", "key": "private_key", "env": "AGENT_BROKER_TLS_PRIVATE_KEY"}
    assert conf.get_str("agents.claude.provider") == "agent_api"
    assert conf.get_str("agents.opencode.container") == "opencode"
    assert conf.get_str("credentials.vault") == ""


def test_certificate_values_have_one_source() -> None:
    """CERT_NAMES and CERT_VALIDITY are only in the configuration of the component client."""
    conf = cfg.ConfigFile(CONFIG_FILE)
    assert conf.section("cert") == {"file": "../client/tls/server.crt"}
