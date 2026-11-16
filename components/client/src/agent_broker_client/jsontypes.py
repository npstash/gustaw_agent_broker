"""The type of a JSON or YAML value. Code that has no more exact type uses these."""
from __future__ import annotations

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


def text_of(body: JsonObject, key: str, default: str = "") -> str:
    """The text in ``body[key]``. ``default`` if the key is missing or the value is no text."""
    value = body.get(key, default)
    return value if isinstance(value, str) else default
