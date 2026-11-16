"""The messages between a client, the broker and an agent (DESIGN.md, section 8.2).

The file messages.schema.json is the contract. It is package data. No project keeps a copy.
A message that does not agree with its schema is an error.
"""
from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from .jsontypes import JsonObject

EVENT_SCHEMAS = {
    "state": "StateEvent",
    "status": "StatusEvent",
    "reply": "ReplyEvent",
    "notice": "NoticeEvent",
    "log": "LogEvent",
}


class MessageError(ValueError):
    """A message does not agree with its schema."""


@lru_cache(maxsize=1)
def schema() -> JsonObject:
    """The schema file as a JSON object."""
    text = resources.files(__package__).joinpath("messages.schema.json").read_text(encoding="utf-8")
    root: JsonObject = json.loads(text)
    return root


def message_names() -> list[str]:
    """The names of all messages in the schema."""
    defs = schema()["$defs"]
    if not isinstance(defs, dict):
        raise MessageError("the schema has no $defs")
    return list(defs)


@lru_cache(maxsize=None)
def _validator(name: str) -> Draft202012Validator:
    root = schema()
    defs = root["$defs"]
    if not isinstance(defs, dict) or name not in defs:
        raise KeyError(f"unknown message '{name}'")
    sub: JsonObject = {"$schema": root["$schema"], "$defs": defs, "$ref": f"#/$defs/{name}"}
    return Draft202012Validator(sub, format_checker=FormatChecker())


def validate[T](name: str, message: T) -> T:
    """Check a message against its schema. Return the message."""
    try:
        _validator(name).validate(message)
    except ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "message"
        raise MessageError(f"{name}: {where}: {exc.message}") from None
    return message


def validate_event[T](event: str, body: T) -> T:
    """Check the body of an event of a stream. ``event`` is the SSE event type."""
    if event not in EVENT_SCHEMAS:
        raise MessageError(f"unknown event type '{event}'")
    return validate(EVENT_SCHEMAS[event], body)


def error_body(error: str, message: str) -> JsonObject:
    """Make a checked ``ErrorResponse``."""
    return validate("ErrorResponse", {"error": error, "message": message})
