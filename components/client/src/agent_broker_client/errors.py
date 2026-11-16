"""The errors of the client library (DESIGN.md, section 6.1, "Errors").

Each error answer of the broker becomes one subclass of BrokerError. ``status`` is the HTTP
status. ``error`` is the error value of the ``ErrorResponse``, or None if the answer has none.
"""
from __future__ import annotations

from .models import ErrorCode


class BrokerError(Exception):
    """The broker did not do a request."""

    def __init__(self, message: str, status: int | None = None, error: ErrorCode | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.error = error


class BrokerUnreachableError(BrokerError):
    """The broker did not answer: no connection, a timeout, or a connection that broke."""


class BrokerTlsError(BrokerUnreachableError):
    """The TLS connection failed, e.g. the certificate or the host name is not correct."""


class StreamTimeoutError(BrokerUnreachableError):
    """A stream gave no data for HEARTBEAT_TIMEOUT."""


class StreamQueueFullError(BrokerError):
    """The program did not read STREAM_QUEUE_LIMIT events of a stream. The client dropped the connection."""


class BadRequestError(BrokerError):
    """``400``: the request is not correct."""


class UnauthorizedError(BrokerError):
    """``401``: the login is not correct."""


class NotFoundError(BrokerError):
    """``404``. ``error`` tells what is missing, e.g. ``not_found`` or ``workspace_not_found``."""


class WorkspaceNotFoundError(NotFoundError):
    """``404`` ``workspace_not_found``: the workspace folder does not exist and ``force`` is false."""


class SessionGoneError(NotFoundError):
    """``404`` for a session stream: the session or the agent does not exist. The client does not connect again."""


class BusyError(BrokerError):
    """``409`` ``busy``: a command runs in the session."""


class AgentUnavailableError(BrokerError):
    """``503`` ``agent_unavailable``: the broker cannot reach the agent."""


class InvalidAnswerError(BrokerError):
    """The answer of the broker does not agree with the schema or has an unexpected status."""


class ClientClosedError(BrokerError):
    """The client is closed."""


def error_for(status: int, code: ErrorCode | None, text: str) -> BrokerError:
    """The error for an error answer of the broker."""
    message = f"{status} {code.value if code is not None else ''}: {text}".replace(" :", ":")
    if status == 400:
        return BadRequestError(message, status, code)
    if status == 401:
        return UnauthorizedError(message, status, code)
    if status == 404:
        if code is ErrorCode.WORKSPACE_NOT_FOUND:
            return WorkspaceNotFoundError(message, status, code)
        return NotFoundError(message, status, code)
    if status == 409:
        return BusyError(message, status, code)
    if status == 503:
        return AgentUnavailableError(message, status, code)
    return InvalidAnswerError(message, status, code)
