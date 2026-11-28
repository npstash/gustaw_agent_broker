"""The sources of the secrets of the broker: the vault entries of local.yaml, vaultkeeper and the environment.

The system names are invented. The fake vaultkeeper has the request and answer lines of DESIGN 8.2 of gustaw_vaultkeeper.
"""
from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from agent_broker_client import config as cfg
from agent_broker_client import dependencies, secrets_proxy
from agent_broker_client.jsontypes import JsonObject
from agent_broker_service import app, settings

SOCKET_NAME = "vaultkeeper.sock"
VAULT = "vault-example"
LOCAL = f"""\
credentials:
  vault: {VAULT}
  vaultkeeper: /proc/self/cwd/{SOCKET_NAME}
  secrets:
    tls_private_key: {{entry: "Example TLS 01"}}
    api_username: {{entry: "Example API 01"}}
    api_password: {{entry: "Example API 01"}}
    opencode_password: {{entry: "Example OpenCode 01"}}
docker:
  hostops: /run/example/hostops.sock
"""


class FakeVaultkeeper:
    """A fake daemon vaultkeeper on a Unix socket. ``systems`` maps (vault, system) to its secrets; else not-granted."""

    def __init__(self, path: Path, systems: Mapping[tuple[str, str], Mapping[str, str]]):
        self.systems = dict(systems)
        self.requests: list[JsonObject] = []
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(path))
        self._server.listen()
        self._server.settimeout(5)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            with conn, conn.makefile("rb") as lines:
                for line in lines:
                    request = json.loads(line)
                    if not isinstance(request, dict):
                        return
                    self.requests.append(request)
                    found = self.systems.get((str(request.get("vault")), str(request.get("system"))))
                    answer: JsonObject = (
                        {"ok": True, "result": {"url": "", "authority_info": "", "secrets": dict(found)}}
                        if found is not None and request.get("op") == "get" and set(request) == {"op", "vault", "system"}
                        else {"ok": False, "error": "not-granted"})
                    conn.sendall(json.dumps(answer).encode() + b"\n")

    def close(self) -> None:
        self._server.close()
        self._thread.join(timeout=5)


@pytest.fixture
def vaultkeeper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeVaultkeeper]:
    monkeypatch.chdir(tmp_path)

    def no_lookup(name: str, start: Path | None = None) -> Path:
        raise dependencies.DependencyError(f"no lookup of {name} with vaultkeeper")
    monkeypatch.setattr(dependencies, "find", no_lookup)
    daemon = FakeVaultkeeper(Path(SOCKET_NAME), {
        (VAULT, "Example TLS 01"): {"private_key": "key-from-vaultkeeper"},
        (VAULT, "Example API 01"): {"username": "user-from-vaultkeeper", "password": "password-from-vaultkeeper"},
        (VAULT, "Example OpenCode 01"): {"password": "opencode-from-vaultkeeper"},
    })
    yield daemon
    daemon.close()


def write_local(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "local.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_local_file_gives_the_vault_entries_and_the_sockets(tmp_path: Path) -> None:
    conf = settings.load(local_path=write_local(tmp_path, LOCAL))
    assert conf.unknown_keys == []
    assert conf.get_str_map("credentials.secrets.api_password") == {
        "key": "password", "env": "AGENT_BROKER_API_PASSWORD", "entry": "Example API 01"}
    assert settings.from_config(conf).hostops_socket == "/run/example/hostops.sock"


@pytest.mark.parametrize("key", ["docker.hostops", "credentials.vaultkeeper"])
def test_sockets_must_be_absolute_paths(tmp_path: Path, key: str) -> None:
    section, name = key.split(".")
    with pytest.raises(cfg.ConfigError, match="absolute socket path"):
        settings.load(local_path=write_local(tmp_path, f"{section}:\n  {name}: relative.sock\n"))


def test_credentials_section_has_the_start_secrets_and_no_empty_vaultkeeper() -> None:
    conf = settings.load(local_path=None)
    section = settings.credentials(conf, ["api_username", "api_password"])
    assert "vaultkeeper" not in section
    secrets = section["secrets"]
    assert isinstance(secrets, dict) and set(secrets) == {"api_username", "api_password"}


def test_broker_reads_its_secrets_through_vaultkeeper(tmp_path: Path, vaultkeeper: FakeVaultkeeper) -> None:
    conf = settings.load(local_path=write_local(tmp_path, LOCAL))
    values = settings.from_config(conf)
    secrets = app.open_broker_secrets(conf, values)
    assert secrets.get_secret("tls_private_key") == "key-from-vaultkeeper"
    assert secrets.get_secret("api_password") == "password-from-vaultkeeper"
    with pytest.raises(secrets_proxy.SecretError):         # the agent opencode is disabled: no OpenCode password
        secrets.get_secret("opencode_password")
    assert {(r["vault"], r["system"]) for r in vaultkeeper.requests} == {(VAULT, "Example TLS 01"), (VAULT, "Example API 01")}


def test_opencode_password_comes_from_vaultkeeper_when_opencode_is_enabled(
        tmp_path: Path, vaultkeeper: FakeVaultkeeper) -> None:
    conf = settings.load(local_path=write_local(tmp_path, LOCAL + "agents:\n  opencode:\n    enabled: true\n"))
    secrets = app.open_broker_secrets(conf, settings.from_config(conf))
    assert secrets.get_secret("opencode_password") == "opencode-from-vaultkeeper"


def test_start_fails_when_vaultkeeper_refuses(tmp_path: Path, vaultkeeper: FakeVaultkeeper) -> None:
    del vaultkeeper.systems[(VAULT, "Example API 01")]
    conf = settings.load(local_path=write_local(tmp_path, LOCAL))
    with pytest.raises(secrets_proxy.SecretError, match="not-granted"):
        app.open_broker_secrets(conf, settings.from_config(conf))


def test_vault_without_the_entry_of_a_secret_fails(tmp_path: Path, vaultkeeper: FakeVaultkeeper) -> None:
    text = LOCAL.replace('    api_username: {entry: "Example API 01"}\n', "")
    conf = settings.load(local_path=write_local(tmp_path, text))
    with pytest.raises(secrets_proxy.SecretError, match="entry"):
        app.open_broker_secrets(conf, settings.from_config(conf))


def test_without_a_vault_the_secrets_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BROKER_TLS_PRIVATE_KEY", "key-from-env")
    monkeypatch.setenv("AGENT_BROKER_API_USERNAME", "user-from-env")
    monkeypatch.setenv("AGENT_BROKER_API_PASSWORD", "password-from-env")
    conf = settings.load(local_path=None)
    secrets = app.open_broker_secrets(conf, settings.from_config(conf))
    assert secrets.get_secret("api_username") == "user-from-env"
