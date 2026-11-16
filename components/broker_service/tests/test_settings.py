"""The configuration, the router, the start values and the small helpers of the service."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from agent_broker_client import config as cfg
from agent_broker_service import api, router, settings, version
from agent_broker_service.agent_api import AgentApiProvider
from agent_broker_service.settings import AgentSettings, Settings


def test_settings_of_the_default_configuration() -> None:
    values = settings.from_config(settings.load(local_path=None))
    assert (values.host, values.port, values.heartbeat_interval, values.queue_limit, values.docker_socket) == (
        "0.0.0.0", 38443, 15.0, 100, "/var/run/docker.sock")
    assert values.agents == (
        AgentSettings("claude", "agent_api", "http://127.0.0.1:38444", "claude_code_server"),
        AgentSettings("opencode", "opencode", "http://127.0.0.1:4096", "opencode"))
    assert values.cert_file == (settings.PROJECT.parent / "client" / "tls" / "server.crt").resolve()
    assert values.reconnect_interval == 5.0
    assert values.docker_control_timeout < 60.0   # the broker answers before AGENT_CONTROL_TIMEOUT of the client


def test_local_file_replaces_values(tmp_path: Path) -> None:
    local = tmp_path / "local.yaml"
    local.write_text("server:\n  port: 40000\nagents:\n  claude:\n    url: http://127.0.0.1:1\n", encoding="utf-8")
    values = settings.from_config(settings.load(local_path=local))
    assert values.port == 40000
    assert values.agents[0].url == "http://127.0.0.1:1"


def test_unknown_provider_is_a_start_error(tmp_path: Path) -> None:
    local = tmp_path / "local.yaml"
    local.write_text("agents:\n  claude:\n    provider: other\n", encoding="utf-8")
    with pytest.raises(cfg.ConfigError, match="unknown provider"):
        settings.load(local_path=local)
    with pytest.raises(cfg.ConfigError, match="unknown provider"):
        router.build(Settings(agents=(AgentSettings("a", "other", "http://x", "c"),)))


def test_opencode_agent_is_skipped_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    conf = Settings(agents=(AgentSettings("claude", "agent_api", "http://127.0.0.1:1", "c1"),
                            AgentSettings("opencode", "opencode", "http://127.0.0.1:2", "c2")))
    with caplog.at_level(logging.WARNING, logger="agent_broker"):
        built = router.build(conf)
    assert [e.name for e in built.entries] == ["claude"]
    assert isinstance(built.entries[0].provider, AgentApiProvider)
    assert built.get("opencode") is None
    assert any("opencode is not implemented" in r.getMessage() for r in caplog.records)


def test_credentials_match() -> None:
    header = "Basic dXNlcjpwYXNz"   # user:pass
    assert api.credentials_match(header, "user", "pass")
    assert not api.credentials_match(header, "user", "other")
    assert not api.credentials_match(header, "other", "pass")
    assert not api.credentials_match(None, "user", "pass")
    assert not api.credentials_match("Basic dXNlcnBhc3M=", "user", "pass")   # no colon
    assert not api.credentials_match("Basic ???", "user", "pass")


def test_version_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(version.ENV_COMMIT, "0123456789abcdef")
    monkeypatch.setenv(version.ENV_DATE, "2026-11-16T10:00:00+00:00")
    monkeypatch.setenv(version.ENV_SUBJECT, "Add the broker")
    assert version.version(settings.PROJECT) == "0123456789ab 2026-11-16T10:00:00+00:00 Add the broker"
    monkeypatch.delenv(version.ENV_COMMIT)
    monkeypatch.delenv(version.ENV_DATE)
    monkeypatch.delenv(version.ENV_SUBJECT)
    assert version.version(settings.PROJECT).split()[0]   # from git, or "unknown" outside a clone
