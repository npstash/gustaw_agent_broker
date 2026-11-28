"""Read and write the YAML configuration of a component. Keep the comments of the user.

A component has default values (committed, in the image) and local values (on the host).
A local value replaces the default value of the same key. A change goes only into the
local file.

A key is a dotted path, for example ``streams.heartbeat_interval_s``. A leaf is a
value that is not a mapping. A list is a leaf.
"""
from __future__ import annotations

import copy
import logging
import os
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from types import EllipsisType

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from .jsontypes import JsonObject, JsonValue


class ConfigError(ValueError):
    """The configuration or a new value is not correct."""


Validator = Callable[[JsonValue], None]


def _yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def plain(value: object) -> JsonValue:
    """Return a copy of a ruamel value as plain dict, list and scalar values."""
    if isinstance(value, Mapping):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    raise ConfigError(f"a value of the type {type(value).__name__} is not a configuration value")


def leaves(data: Mapping[str, object], prefix: str = "") -> Iterator[tuple[str, JsonValue]]:
    """Give each leaf as (dotted key, plain value), in file order."""
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, Mapping) and value:
            yield from leaves(value, path + ".")
        else:
            yield path, plain(value)


def parse_value(text: str, current: JsonValue) -> JsonValue:
    """Convert the text of a menu entry to the type of the current value.

    A list takes items separated by commas. Each item gets the type of the
    first current item, or stays a string.
    """
    text = text.strip()
    if isinstance(current, list):
        if text in ("", "[]"):
            return []
        sample = current[0] if current else ""
        return [parse_value(item, sample) for item in text.split(",")]
    if isinstance(current, bool):
        lowered = text.lower()
        if lowered in ("true", "yes", "on", "1"):
            return True
        if lowered in ("false", "no", "off", "0"):
            return False
        raise ConfigError(f"'{text}' is not true or false")
    if isinstance(current, int):
        try:
            return int(text)
        except ValueError:
            raise ConfigError(f"'{text}' is not an integer") from None
    if isinstance(current, float):
        try:
            return float(text)
        except ValueError:
            raise ConfigError(f"'{text}' is not a number") from None
    if isinstance(current, dict):
        raise ConfigError("this key holds a group of values; set its parts one by one")
    if current is None:
        return text
    return text


def check_type(value: JsonValue, current: JsonValue) -> JsonValue:
    """Make sure that a value from JSON has the type of the current value. Return the value."""
    if isinstance(current, bool):
        if not isinstance(value, bool):
            raise ConfigError("the value must be true or false")
    elif isinstance(current, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError("the value must be an integer")
    elif isinstance(current, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError("the value must be a number")
        value = float(value)
    elif isinstance(current, str):
        if not isinstance(value, str):
            raise ConfigError("the value must be a string")
    elif isinstance(current, list):
        if not isinstance(value, list):
            raise ConfigError("the value must be a list")
        if current:
            value = [check_type(item, current[0]) for item in value]
    elif isinstance(current, dict):
        if not isinstance(value, dict):
            raise ConfigError("the value must be a mapping")
    return value


def _load(path: Path) -> CommentedMap:
    with path.open("r", encoding="utf-8") as handle:
        data = _yaml().load(handle) or CommentedMap()
    if not isinstance(data, CommentedMap):
        raise ConfigError(f"{path}: the top level must be a mapping")
    return data


def _child(node: object, part: str) -> object:
    """The value of `part` in a mapping node. Raise KeyError if `node` is no mapping or has no `part`."""
    if not isinstance(node, Mapping) or part not in node:
        raise KeyError(part)
    return node[part]


class ConfigFile:
    """The configuration of one component: a file of default values and an optional local file.

    Without ``local_path``, the default file is the only file, and ``set`` writes it.
    With ``local_path``, ``set`` writes only the local file; the default file is read only.
    ``unknown_keys`` lists the local keys that the default file does not have; they are
    not used. A local file can add a field to a mapping value that has a validator, e.g. the
    vault entry of a secret reference. ``validators`` maps a dotted key to a function that raises
    ConfigError for a value that is not permitted.

    ``get`` gives a value of any type. ``get_str``, ``get_int``, ``get_float``, ``get_bool``
    and ``get_list``, ``get_str_map`` check the type of the value and raise ConfigError if it differs.
    """

    def __init__(self, path: Path | str, validators: Mapping[str, Validator] | None = None,
                 local_path: Path | str | None = None):
        self.path = Path(path)
        self.local_path = Path(local_path) if local_path is not None else None
        self.validators = dict(validators or {})
        if not self.path.is_file():
            raise ConfigError(f"configuration file not found: {self.path}")
        self.data = _load(self.path)
        self.local: CommentedMap | None = None
        self.unknown_keys: list[str] = []
        if self.local_path is not None:
            self.local = _load(self.local_path) if self.local_path.is_file() else CommentedMap()
            for key, _ in leaves(self.local):
                try:
                    node, last = self._parent(key)
                except KeyError:
                    found = self._value_field(key)
                    if found is None:
                        self.unknown_keys.append(key)
                        continue
                    node, last = found
                if last in node and isinstance(node[last], Mapping) and node[last]:
                    self.unknown_keys.append(key)
                    continue
                node[last] = copy.deepcopy(self._local_node(key))
            for key in self.unknown_keys:
                logging.getLogger("agent_broker.config").warning(
                    "%s: the key %s is not a configuration key; the value is not used", self.local_path, key)
        self.validate_all()

    def _value_field(self, key: str) -> tuple[CommentedMap, str] | None:
        """The parent mapping and the name of a new field of a mapping value, or None.

        A mapping value is a mapping with a validator, e.g. a secret reference. A local file can add a
        field to it, e.g. the vault entry. The validator then checks the merged mapping.
        """
        parent, _, last = key.rpartition(".")
        if parent not in self.validators:
            return None
        try:
            node, name = self._parent(parent)
        except KeyError:
            return None
        value = node[name]
        if not isinstance(value, CommentedMap):
            return None
        return value, last

    def _local_node(self, key: str) -> object:
        node: object = self.local
        for part in key.split("."):
            node = _child(node, part)
        return node

    def validate_all(self) -> None:
        for key, check in self.validators.items():
            try:
                value = self.get(key)
            except KeyError:
                raise ConfigError(f"{self.path}: the key {key} is missing") from None
            try:
                check(value)
            except ConfigError as exc:
                raise ConfigError(f"{self.path}: {key}: {exc}") from None

    def _parent(self, key: str) -> tuple[CommentedMap, str]:
        parts = key.split(".")
        node: object = self.data
        for part in parts[:-1]:
            node = _child(node, part)
        if not isinstance(node, CommentedMap) or parts[-1] not in node:
            raise KeyError(key)
        return node, parts[-1]

    def get(self, key: str, default: JsonValue | EllipsisType = ...) -> JsonValue:
        """Give the plain value of a dotted key."""
        try:
            node, last = self._parent(key)
        except KeyError:
            if isinstance(default, EllipsisType):
                raise
            return default
        return plain(node[last])

    def get_str(self, key: str) -> str:
        return as_str(key, self.get(key))

    def get_int(self, key: str) -> int:
        return as_int(key, self.get(key))

    def get_float(self, key: str) -> float:
        return as_float(key, self.get(key))

    def get_bool(self, key: str) -> bool:
        return as_bool(key, self.get(key))

    def get_list(self, key: str) -> list[JsonValue]:
        return as_list(key, self.get(key))

    def get_str_map(self, key: str) -> dict[str, str]:
        return as_str_map(key, self.get(key))

    def section(self, key: str) -> JsonObject:
        value = self.get(key)
        if not isinstance(value, dict):
            raise ConfigError(f"{key} is not a mapping")
        return value

    def items(self) -> list[tuple[str, JsonValue]]:
        """Give each leaf as (dotted key, plain value), in file order."""
        return list(leaves(self.data))

    def set(self, key: str, value: JsonValue, save: bool = True) -> JsonValue:
        """Set an existing key. Check the type and the validator. Save the file. Return the value."""
        node, last = self._parent(key)
        current = plain(node[last])
        value = check_type(value, current)
        check = self.validators.get(key)
        if check is not None:
            check(value)
        node[last] = self._yaml_value(value, node[last])
        if self.local is not None:
            self._set_local(key, value)
        if save:
            self.save()
        return value

    @staticmethod
    def _yaml_value(value: JsonValue, old: object) -> object:
        if isinstance(value, list):
            seq = CommentedSeq(value)
            if isinstance(old, CommentedSeq) and old.fa.flow_style():
                seq.fa.set_flow_style()
            return seq
        return value

    def _set_local(self, key: str, value: JsonValue) -> None:
        """Write the value into the local document. Make the parent mappings if necessary."""
        parts = key.split(".")
        node = self.local
        if node is None:
            raise ConfigError("there is no local file")
        default_node = _load(self.path)
        for part in parts[:-1]:
            default_node = default_node[part]
            if part not in node or not isinstance(node[part], Mapping):
                node[part] = CommentedMap()
            node = node[part]
        old = node[parts[-1]] if parts[-1] in node else default_node[parts[-1]]
        node[parts[-1]] = self._yaml_value(value, old)

    def save(self) -> None:
        """Write the local file (or, without one, the default file) through a temporary file."""
        if self.local is not None and self.local_path is not None:
            target, data = self.local_path, self.local
        else:
            target, data = self.path, self.data
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            _yaml().dump(data, handle)
        os.replace(tmp, target)


def as_str(key: str, value: JsonValue) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{key} is not a string")
    return value


def as_int(key: str, value: JsonValue) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{key} is not an integer")
    return value


def as_float(key: str, value: JsonValue) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{key} is not a number")
    return float(value)


def as_bool(key: str, value: JsonValue) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{key} is not true or false")
    return value


def as_str_list(key: str, value: JsonValue) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{key} is not a list of strings")
    return [v for v in value if isinstance(v, str)]


def as_str_map(key: str, value: JsonValue) -> dict[str, str]:
    if not isinstance(value, dict) or not all(isinstance(v, str) for v in value.values()):
        raise ConfigError(f"{key} is not a mapping of strings")
    return {k: v for k, v in value.items() if isinstance(v, str)}


def as_list(key: str, value: JsonValue) -> list[JsonValue]:
    if not isinstance(value, list):
        raise ConfigError(f"{key} is not a list")
    return value


class StartValues:
    """Give the values that the running process uses.

    A key for which ``at_restart(key)`` is true keeps the value from the start of the
    process. A menu can change the file, but the change applies only after a restart.
    The other keys give the current value of the file. The typed getters work as in ConfigFile.
    """

    def __init__(self, conf: ConfigFile, at_restart: Callable[[str], bool]):
        self.conf = conf
        self.at_restart = at_restart
        self._start = plain(conf.data)

    def get(self, key: str, default: JsonValue | EllipsisType = ...) -> JsonValue:
        if not self.at_restart(key):
            return self.conf.get(key, default)
        node: JsonValue = self._start
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                if isinstance(default, EllipsisType):
                    raise KeyError(key)
                return default
            node = node[part]
        return copy.deepcopy(node)

    def get_str(self, key: str) -> str:
        return as_str(key, self.get(key))

    def get_int(self, key: str) -> int:
        return as_int(key, self.get(key))

    def get_float(self, key: str) -> float:
        return as_float(key, self.get(key))

    def get_bool(self, key: str) -> bool:
        return as_bool(key, self.get(key))

    def get_list(self, key: str) -> list[JsonValue]:
        return as_list(key, self.get(key))

    def get_str_map(self, key: str) -> dict[str, str]:
        return as_str_map(key, self.get(key))

    def section(self, key: str) -> JsonObject:
        value = self.get(key)
        if not isinstance(value, dict):
            raise ConfigError(f"{key} is not a mapping")
        return value


# Validators that the components use.

def one_of(*allowed: JsonValue) -> Validator:
    def check(value: JsonValue) -> None:
        if value not in allowed:
            raise ConfigError(f"the value must be one of: {', '.join(str(a) for a in allowed)}")
    return check


def number_range(low: float | None = None, high: float | None = None) -> Validator:
    def check(value: JsonValue) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError("the value must be a number")
        if low is not None and value < low:
            raise ConfigError(f"the value must be {low} or more")
        if high is not None and value > high:
            raise ConfigError(f"the value must be {high} or less")
    return check


LOG_LEVELS = ("ERROR", "WARNING", "INFO", "DEBUG")


def log_level(value: JsonValue) -> None:
    if not isinstance(value, str) or value.upper() not in LOG_LEVELS:
        raise ConfigError(f"the level must be one of: {', '.join(LOG_LEVELS)}")


def secret_ref(value: JsonValue) -> None:
    """A secret reference: the vault entry and its key, and the environment variable.

    The committed configuration has no vault entry: the local file of the host adds it. Thus a reference needs
    the key or the environment variable. ``secrets_proxy`` stops if a vault needs the entry and it is missing.
    """
    fields = ("entry", "key", "env")
    if not (isinstance(value, dict) and value and set(value) <= set(fields)
            and all(isinstance(v, str) and v for v in value.values())
            and ("env" in value or "key" in value)):
        raise ConfigError("a secret reference must be {entry: <vault entry>, key: <secret name>, env: <variable>}")
