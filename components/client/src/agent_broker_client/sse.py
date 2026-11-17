"""Server-Sent Events (DESIGN.md, section 6.1.1, "Streams").

The field ``event`` holds the type. The field ``data`` holds the JSON body. Events have no ``id``.
A line that starts with ``:`` is a comment, e.g. the heartbeat. The decoder gives the raw data
text. The caller decodes the JSON, so that a bad body is an error of one event only.
"""
from __future__ import annotations

import json

from .jsontypes import JsonValue

COMMENT = b": keep-alive\n\n"


def encode(event: str, body: JsonValue) -> bytes:
    """One event as SSE bytes."""
    data = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n".encode("utf-8")


class Decoder:
    """Collect lines and give each complete event as (type, data text)."""

    def __init__(self) -> None:
        self._event = ""
        self._data: list[str] = []
        self._buffer = ""

    def feed(self, text: str) -> list[tuple[str, str]]:
        self._buffer += text
        events: list[tuple[str, str]] = []
        while True:
            index = self._buffer.find("\n")
            if index < 0:
                break
            line = self._buffer[:index].removesuffix("\r")
            self._buffer = self._buffer[index + 1:]
            event = self._line(line)
            if event is not None:
                events.append(event)
        return events

    def _line(self, line: str) -> tuple[str, str] | None:
        if line == "":
            if not self._data and not self._event:
                return None
            event = self._event or "message"
            data = "\n".join(self._data)
            self._event, self._data = "", []
            return event, data
        if line.startswith(":"):
            return None
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            self._event = value
        elif field == "data":
            self._data.append(value)
        return None
