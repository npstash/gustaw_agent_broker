"""Log lines of the broker service: time, level, component and message.

Each component is a logger ``agent_broker.<component>``. Use ``get(component)``.
The lines go to the standard error output. Docker keeps them as the log of the container.
"""
from __future__ import annotations

import logging
import sys
from typing import TextIO

ROOT = "agent_broker"
FORMAT = "%(asctime)s %(levelname)s %(component)s %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _ComponentFilter(logging.Filter):
    """Give each line the component name: the logger name without ``agent_broker.``."""

    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name
        record.component = name[len(ROOT) + 1:] if name.startswith(ROOT + ".") else name
        return True


def get(component: str) -> logging.Logger:
    return logging.getLogger(f"{ROOT}.{component}")


def setup(level: str = "INFO", stream: TextIO | None = None) -> logging.Handler:
    """Send the lines of all components at ``level`` and higher to ``stream`` (default: standard error)."""
    root = logging.getLogger(ROOT)
    root.setLevel(level)
    root.propagate = False
    for old in list(root.handlers):
        root.removeHandler(old)
        old.close()
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(logging.Formatter(FORMAT, DATE_FORMAT))
    handler.addFilter(_ComponentFilter())
    root.addHandler(handler)
    return handler
