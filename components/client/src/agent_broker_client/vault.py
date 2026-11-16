"""Secrets of the Agent Broker. The only real source is the vault (maya/tools/auth).

The configuration of each component has this section (DESIGN.md, section 8.3)::

    credentials:
      vault: vault-agent_broker
      secrets:
        <name>: {entry: "<vault entry>", key: "<secret key>"}

The vault and the vault tool (maya/tools/auth) are found with dependencies.py (rule
submodule-dependencies of the repository maya-agent-kit). A component opens its secrets at start. Tests use FakeSecrets.
"""
from __future__ import annotations

import importlib
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Protocol

from . import dependencies
from .jsontypes import JsonObject


class SecretError(RuntimeError):
    """A secret is not available."""


class Secrets(Protocol):
    def get(self, name: str) -> str: ...


class FakeSecrets:
    """Secrets in memory, for tests."""

    def __init__(self, values: Mapping[str, str]):
        self._values = dict(values)

    def get(self, name: str) -> str:
        try:
            return self._values[name]
        except KeyError:
            raise SecretError(f"no secret '{name}'") from None


def find(name: str, start: Path | None = None) -> Path:
    """The folder of the shareable submodule ``name``. ``start`` is the folder of the component."""
    try:
        return dependencies.find(name, start)
    except dependencies.DependencyError as exc:
        raise SecretError(str(exc)) from None


def vault_tool(start: Path | None = None) -> Path:
    """The folder of the vault tool: tools/auth in the repository maya-agent-kit."""
    return find("maya-agent-kit", start) / "tools" / "auth"


def load_credstore(tool_dir: Path | None = None) -> ModuleType:
    """Import the credstore module of the vault tool in ``tool_dir`` (default: found by vault_tool())."""
    tool = Path(tool_dir) if tool_dir is not None else vault_tool()
    src = tool / "src"
    if not (src / "credstore.py").is_file():
        raise SecretError(f"the vault tool was not found in {tool}")
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    return importlib.import_module("credstore")


def resolve_path(base_dir: Path, value: str) -> Path:
    """Resolve a path from a configuration file. A relative path starts at the project folder."""
    path = Path(value)
    if not path.is_absolute():
        path = (Path(base_dir) / path).resolve()
    return path


class _Credential(Protocol):
    """What this module reads from a credential of the vault tool."""

    def secrets(self) -> Mapping[str, str]: ...


class VaultSecrets:
    """Secrets from the vault. ``open()`` reads all of them at once."""

    def __init__(self, vault_home: Path, refs: Mapping[str, Mapping[str, str]], tool_dir: Path | None = None):
        self.vault_home = Path(vault_home)
        self.tool_dir = tool_dir
        self.refs = {name: dict(ref) for name, ref in refs.items()}
        self._values: dict[str, str] = {}

    def open(self, names: list[str] | None = None) -> "VaultSecrets":
        credstore = load_credstore(self.tool_dir)
        cache: dict[str, _Credential] = {}
        for name in names if names is not None else list(self.refs):
            ref = self.refs.get(name)
            if ref is None:
                raise SecretError(f"the configuration names no vault entry for '{name}'")
            entry, key = ref["entry"], ref["key"]
            try:
                if entry not in cache:
                    cache[entry] = credstore.get_credential(entry, vault_home=self.vault_home)
                value = cache[entry].secrets().get(key)
            except credstore.CredentialError as exc:
                raise SecretError(f"vault entry '{entry}': {exc}") from None
            if not value:
                raise SecretError(f"vault entry '{entry}' has no value for '{key}'")
            self._values[name] = value
        return self

    def get(self, name: str) -> str:
        try:
            return self._values[name]
        except KeyError:
            raise SecretError(f"the secret '{name}' is not open") from None


def from_config(base_dir: Path, credentials: JsonObject) -> VaultSecrets:
    """Make a VaultSecrets from the ``credentials`` section. ``base_dir`` is the folder of the component."""
    vault = credentials["vault"]
    secrets = credentials.get("secrets", {})
    if not isinstance(vault, str) or not isinstance(secrets, dict):
        raise SecretError("the credentials section needs a vault name and a secrets mapping")
    refs: dict[str, Mapping[str, str]] = {}
    for name, ref in secrets.items():
        if not isinstance(ref, dict) or not all(isinstance(v, str) for v in ref.values()):
            raise SecretError(f"the secret reference '{name}' must be a mapping of strings")
        refs[name] = {k: v for k, v in ref.items() if isinstance(v, str)}
    return VaultSecrets(find(vault, base_dir), refs, vault_tool(base_dir))
