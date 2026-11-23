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
        AgentSettings("claude", "agent_api", "http://127.0.0.1:38444", "claude_code_server", enabled=True),
        AgentSettings("opencode", "opencode", "http://127.0.0.1:4096", "opencode", enabled=False,
                      workspace_root="/home/opencode/workspace"))
    assert values.log_level == "INFO"
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
    assert any("opencode is not available" in r.getMessage() for r in caplog.records)


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


def test_local_file_can_change_each_value_of_the_two_agents(tmp_path: Path) -> None:
    local = tmp_path / "local.yaml"
    local.write_text("agents:\n  opencode:\n    enabled: true\n    url: http://127.0.0.1:9\n    container: oc\n"
                     "  claude:\n    enabled: false\n    provider: opencode\n", encoding="utf-8")
    claude, opencode = settings.from_config(settings.load(local_path=local)).agents
    assert claude == AgentSettings("claude", "opencode", "http://127.0.0.1:38444", "claude_code_server", enabled=False)
    assert opencode == AgentSettings("opencode", "opencode", "http://127.0.0.1:9", "oc", enabled=True,
                                     workspace_root="/home/opencode/workspace")


def test_local_file_cannot_add_an_agent(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    local = tmp_path / "local.yaml"
    local.write_text("agents:\n  third:\n    enabled: true\n    provider: agent_api\n    url: http://x\n"
                     "    container: c\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="agent_broker"):
        values = settings.from_config(settings.load(local_path=local))
    assert [a.name for a in values.agents] == ["claude", "opencode"]
    assert any("agents.third" in r.getMessage() for r in caplog.records)


def test_enabled_must_be_true_or_false(tmp_path: Path) -> None:
    local = tmp_path / "local.yaml"
    local.write_text("agents:\n  claude:\n    enabled: maybe\n", encoding="utf-8")
    with pytest.raises(cfg.ConfigError, match="enabled"):
        settings.load(local_path=local)


def test_disabled_agent_is_not_in_the_router_and_gives_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    conf = Settings(agents=(AgentSettings("claude", "agent_api", "http://127.0.0.1:1", "c1", enabled=False),
                            AgentSettings("opencode", "opencode", "http://127.0.0.1:2", "c2", enabled=False,
                                          workspace_root="/home/opencode/workspace")))
    with caplog.at_level(logging.WARNING, logger="agent_broker"):
        built = router.build(conf)
    assert built.entries == [] and built.get("claude") is None
    assert not caplog.records


def test_log_level_is_a_configuration_value(tmp_path: Path) -> None:
    local = tmp_path / "local.yaml"
    local.write_text("log:\n  level: debug\n", encoding="utf-8")
    assert settings.from_config(settings.load(local_path=local)).log_level == "DEBUG"
    local.write_text("log:\n  level: LOUD\n", encoding="utf-8")
    with pytest.raises(cfg.ConfigError, match="level"):
        settings.load(local_path=local)
