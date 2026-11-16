"""The messages between a client, the broker and an agent (DESIGN.md, section 8.2).

The file messages.schema.json is the contract. It is package data. No project keeps a copy.
A message that does not agree with its schema is an error.
"""
from __future__ import annotations

import datetime
import json
import re
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


DATE_TIME = re.compile(
    r"\d{4}-\d{2}-\d{2}[Tt]([01]\d|2[0-3]):[0-5]\d:([0-5]\d|60)(\.\d+)?([Zz]|[+-]([01]\d|2[0-3]):[0-5]\d)",
    re.ASCII)


def parse_date_time(value: str) -> datetime.datetime:
    """The date and time of an RFC 3339 text. Raise ValueError for other text.

    ``datetime`` has no leap second. Second 60 becomes second 59.
    """
    match = DATE_TIME.fullmatch(value)
    if match is None:
        raise ValueError(f"not an RFC 3339 date and time: {value!r}")
    text = value.upper().replace("Z", "+00:00")
    if match.group(2) == "60":
        text = text[:17] + "59" + text[19:]
    return datetime.datetime.fromisoformat(text)


def is_date_time(value: object) -> bool:
    """True for an RFC 3339 date and time (format ``date-time``). Other types pass.

    jsonschema checks ``date-time`` only with an extra package. This function replaces it.
    """
    if not isinstance(value, str):
        return True
    try:
        parse_date_time(value)
    except ValueError:
        return False
    return True


def _format_checker() -> FormatChecker:
    checker = FormatChecker()
    checker.checks("date-time")(is_date_time)
    return checker


@lru_cache(maxsize=None)
def _validator(name: str) -> Draft202012Validator:
    root = schema()
    defs = root["$defs"]
    if not isinstance(defs, dict) or name not in defs:
        raise KeyError(f"unknown message '{name}'")
    sub: JsonObject = {"$schema": root["$schema"], "$defs": defs, "$ref": f"#/$defs/{name}"}
    return Draft202012Validator(sub, format_checker=_format_checker())


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
