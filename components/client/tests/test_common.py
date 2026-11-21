"""The helper modules: configuration, vault access and the lookup of the submodules."""
from pathlib import Path

import pytest

from agent_broker_client import config as cfg
from agent_broker_client import dependencies, vault

SAMPLE = """\
# top comment
streams:
  heartbeat_interval_s: 15   # idle time
cert:
  names: [a, b]
"""


def test_config_get_set_keeps_comments(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(SAMPLE, encoding="utf-8")
    conf = cfg.ConfigFile(path)
    assert conf.get_int("streams.heartbeat_interval_s") == 15
    assert conf.get_list("cert.names") == ["a", "b"]
    conf.set("streams.heartbeat_interval_s", 20)
    text = path.read_text(encoding="utf-8")
    assert "# top comment" in text and "# idle time" in text and "heartbeat_interval_s: 20" in text
    with pytest.raises(cfg.ConfigError):
        conf.set("streams.heartbeat_interval_s", "x")
    with pytest.raises(cfg.ConfigError):
        cfg.ConfigFile(tmp_path / "missing.yaml")


def test_config_validator_at_load(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(SAMPLE, encoding="utf-8")
    with pytest.raises(cfg.ConfigError):
        cfg.ConfigFile(path, {"streams.heartbeat_interval_s": cfg.number_range(high=10)})
    with pytest.raises(cfg.ConfigError):
        cfg.ConfigFile(path, {"no.such.key": cfg.log_level})


def test_secret_ref() -> None:
    cfg.secret_ref({"entry": "a", "key": "b"})
    cfg.secret_ref({"env": "A_VARIABLE"})
    with pytest.raises(cfg.ConfigError):
        cfg.secret_ref({"entry": "a"})


def test_fake_secrets() -> None:
    secrets = vault.FakeSecrets({"a": "1"})
    assert secrets.get_secret("a") == "1"
    with pytest.raises(vault.SecretError):
        secrets.get_secret("b")


def test_vault_find_gives_a_secret_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = dependencies.roots   # the search goes up to the roots above; tmp_path can be in a repository
    monkeypatch.setattr(dependencies, "roots", lambda start=None: (r for r in real(start) if r == tmp_path))
    (tmp_path / "dependencies.txt").write_text("[maya-agent-kit]\n../nothing\n", encoding="utf-8")
    with pytest.raises(vault.SecretError, match="maya-agent-kit is not found"):
        vault.vault_tool(tmp_path)


def test_project_dependencies_file_finds_both_submodules() -> None:
    project = Path(__file__).resolve().parents[3]
    sections = dependencies.dependencies(project)
    assert set(sections) == {"maya-agent-kit", "vault-agent_broker"}
    assert dependencies.find("maya-agent-kit", Path(__file__).resolve().parents[1]).is_dir()


def test_dependencies_is_a_copy_of_the_reference_code() -> None:
    maya_agent_kit = dependencies.find("maya-agent-kit", Path(__file__).resolve().parents[1])
    reference = (maya_agent_kit / "rules" / "submodule-dependencies.py").read_text(encoding="utf-8")
    assert Path(dependencies.__file__).read_text(encoding="utf-8") == reference


def test_resolve_path(tmp_path: Path) -> None:
    assert vault.resolve_path(tmp_path, "x/y") == (tmp_path / "x" / "y").resolve()
    assert vault.resolve_path(tmp_path, str(tmp_path)) == tmp_path
