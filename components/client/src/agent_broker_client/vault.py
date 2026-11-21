"""The vault tool for certtool, the path of a configuration file, and the secrets of the tests.

A component reads its secrets with secrets_proxy. certtool writes the private key into the vault with the
vault tool (maya/tools/auth). The vault and the vault tool are found with dependencies.py (rule
submodule-dependencies of the repository maya-agent-kit). Tests use FakeSecrets.
"""
from __future__ import annotations

import importlib
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

from . import dependencies
from .secrets_proxy import SecretError, Secrets

__all__ = ["FakeSecrets", "SecretError", "Secrets", "find", "load_credstore", "resolve_path", "vault_tool"]


class FakeSecrets(Secrets):
    """Secrets in memory, for tests."""

    def __init__(self, values: Mapping[str, str]):
        self._values = dict(values)

    def get_secret(self, name: str) -> str:
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
