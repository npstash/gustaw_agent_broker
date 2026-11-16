"""Typed objects for the messages of the broker API (DESIGN.md, sections 6.1 and 8.2).

Each ``parse_*`` function checks a JSON body against the schema first. Then it makes the
typed object. A body that does not agree with the schema raises MessageError.
"""
from __future__ import annotations

import datetime
import enum
import json
from dataclasses import dataclass

from . import messages
from .jsontypes import JsonObject, JsonValue
from .messages import MessageError


class SessionState(enum.StrEnum):
    IDLE = "idle"
    WORKING = "working"
    STOPPED = "stopped"


class ContainerState(enum.StrEnum):
    """The Docker state of the container of an agent. MISSING: no container exists."""
    RUNNING = "running"
    EXITED = "exited"
    RESTARTING = "restarting"
    PAUSED = "paused"
    CREATED = "created"
    DEAD = "dead"
    MISSING = "missing"


class AgentState(enum.StrEnum):
    OK = "ok"
    UNREACHABLE = "unreachable"


class Provider(enum.StrEnum):
    AGENT_API = "agent_api"
    OPENCODE = "opencode"


class CommandMode(enum.StrEnum):
    VOICE = "voice"
    TEXT = "text"


class CommandStatus(enum.StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"


class Applied(enum.StrEnum):
    NOW = "now"
    AT_RESTART = "at_restart"


class ErrorCode(enum.StrEnum):
    BAD_REQUEST = "bad_request"
    UNAUTHORIZED = "unauthorized"
    NOT_FOUND = "not_found"
    BUSY = "busy"
    WORKSPACE_NOT_FOUND = "workspace_not_found"
    AGENT_UNAVAILABLE = "agent_unavailable"


class StatusKind(enum.StrEnum):
    ACTIVITY = "activity"


class NoticeKind(enum.StrEnum):
    LIFECYCLE = "lifecycle"
    COMMAND_FAILED = "command_failed"
    COMMAND_DROPPED = "command_dropped"


class LogLevel(enum.StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"
    DEBUG = "DEBUG"


@dataclass(frozen=True)
class AgentInfo:
    """One item of ``AgentList``."""
    name: str
    provider: Provider
    container_state: ContainerState
    agent_state: AgentState
    version: str


@dataclass(frozen=True)
class AgentResult:
    agent: str
    container_state: ContainerState


@dataclass(frozen=True)
class ConfigValue:
    key: str
    value: JsonValue


@dataclass(frozen=True)
class ConfigSetResult:
    key: str
    value: JsonValue
    applied: Applied


@dataclass(frozen=True)
class Session:
    id: str
    agent: str
    client: str | None
    workspace: str
    workspace_path: str
    state: SessionState
    title: str | None
    created_at: datetime.datetime
    last_modified: datetime.datetime


@dataclass(frozen=True)
class CommandAccepted:
    id: str
    status: CommandStatus


@dataclass(frozen=True)
class SessionResult:
    session_state: SessionState


@dataclass(frozen=True)
class StateEvent:
    agent: str
    session: str
    state: SessionState


@dataclass(frozen=True)
class StatusEvent:
    agent: str
    session: str
    kind: StatusKind
    text: str


@dataclass(frozen=True)
class ReplyEvent:
    agent: str
    session: str
    command_id: str
    message: str
    language: str


@dataclass(frozen=True)
class NoticeEvent:
    """``session`` is None in an event of the broker stream."""
    agent: str
    session: str | None
    kind: NoticeKind
    command_id: str | None
    text: str


@dataclass(frozen=True)
class LogEvent:
    agent: str
    component: str
    level: LogLevel
    time: str
    message: str


@dataclass(frozen=True)
class InvalidEvent:
    """An event of a stream that does not agree with the schema. The stream continues."""
    event: str
    data: str
    error: MessageError


type SessionEvent = StateEvent | StatusEvent | ReplyEvent | NoticeEvent
type BrokerEvent = NoticeEvent | LogEvent

SESSION_EVENTS: frozenset[str] = frozenset({"state", "status", "reply", "notice"})
BROKER_EVENTS: frozenset[str] = frozenset({"notice", "log"})


# --- Typed access to a checked body ------------------------------------------------

def _object(name: str, value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        raise MessageError(f"{name}: the message is not an object")
    return value


def _str(body: JsonObject, key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        raise MessageError(f"{key}: not a string")
    return value


def _opt_str(body: JsonObject, key: str) -> str | None:
    value = body.get(key)
    if value is not None and not isinstance(value, str):
        raise MessageError(f"{key}: not a string or null")
    return value


def _time(body: JsonObject, key: str) -> datetime.datetime:
    try:
        return messages.parse_date_time(_str(body, key))
    except ValueError as exc:
        raise MessageError(f"{key}: {exc}") from None


def _checked(name: str, body: JsonValue) -> JsonObject:
    return _object(name, messages.validate(name, body))


def _agent(item: JsonObject) -> AgentInfo:
    return AgentInfo(
        name=_str(item, "name"), provider=Provider(_str(item, "provider")),
        container_state=ContainerState(_str(item, "container_state")),
        agent_state=AgentState(_str(item, "agent_state")), version=_str(item, "version"))


def _session(item: JsonObject) -> Session:
    return Session(
        id=_str(item, "id"), agent=_str(item, "agent"), client=_opt_str(item, "client"),
        workspace=_str(item, "workspace"), workspace_path=_str(item, "workspace_path"),
        state=SessionState(_str(item, "state")), title=_opt_str(item, "title"),
        created_at=_time(item, "created_at"), last_modified=_time(item, "last_modified"))


def _items(body: JsonObject, key: str) -> list[JsonObject]:
    value = body.get(key)
    if not isinstance(value, list):
        raise MessageError(f"{key}: not a list")
    return [_object(key, item) for item in value]


def parse_agent_list(body: JsonValue) -> list[AgentInfo]:
    return [_agent(item) for item in _items(_checked("AgentList", body), "agents")]


def parse_agent_result(body: JsonValue) -> AgentResult:
    checked = _checked("AgentResult", body)
    return AgentResult(agent=_str(checked, "agent"), container_state=ContainerState(_str(checked, "container_state")))


def parse_config_values(body: JsonValue) -> list[ConfigValue]:
    return [ConfigValue(key=_str(item, "key"), value=item["value"])
            for item in _items(_checked("ConfigValues", body), "values")]


def parse_config_value(body: JsonValue) -> ConfigValue:
    checked = _checked("ConfigValue", body)
    return ConfigValue(key=_str(checked, "key"), value=checked["value"])


def parse_config_set_result(body: JsonValue) -> ConfigSetResult:
    checked = _checked("ConfigSetResult", body)
    return ConfigSetResult(key=_str(checked, "key"), value=checked["value"], applied=Applied(_str(checked, "applied")))


def parse_session(body: JsonValue) -> Session:
    return _session(_checked("Session", body))


def parse_session_list(body: JsonValue) -> list[Session]:
    return [_session(item) for item in _items(_checked("SessionList", body), "sessions")]


def parse_command_accepted(body: JsonValue) -> CommandAccepted:
    checked = _checked("CommandAccepted", body)
    return CommandAccepted(id=_str(checked, "id"), status=CommandStatus(_str(checked, "status")))


def parse_session_result(body: JsonValue) -> SessionResult:
    return SessionResult(session_state=SessionState(_str(_checked("SessionResult", body), "session_state")))


def parse_error(body: JsonValue) -> tuple[ErrorCode, str] | None:
    """The error value and the text of an ``ErrorResponse``. None if the body is no ErrorResponse."""
    try:
        checked = _checked("ErrorResponse", body)
    except MessageError:
        return None
    return ErrorCode(_str(checked, "error")), _str(checked, "message")


def parse_event(event: str, body: JsonValue) -> StateEvent | StatusEvent | ReplyEvent | NoticeEvent | LogEvent:
    """Make the typed object of a checked event body. ``event`` is the SSE event type."""
    checked = _object(event, messages.validate_event(event, body))
    agent = _str(checked, "agent")
    if event == "state":
        return StateEvent(agent=agent, session=_str(checked, "session"), state=SessionState(_str(checked, "state")))
    if event == "status":
        return StatusEvent(agent=agent, session=_str(checked, "session"), kind=StatusKind(_str(checked, "kind")),
                           text=_str(checked, "text"))
    if event == "reply":
        return ReplyEvent(agent=agent, session=_str(checked, "session"), command_id=_str(checked, "command_id"),
                          message=_str(checked, "message"), language=_str(checked, "language"))
    if event == "notice":
        return NoticeEvent(agent=agent, session=_opt_str(checked, "session"), kind=NoticeKind(_str(checked, "kind")),
                           command_id=_opt_str(checked, "command_id"), text=_str(checked, "text"))
    return LogEvent(agent=agent, component=_str(checked, "component"), level=LogLevel(_str(checked, "level")),
                    time=_str(checked, "time"), message=_str(checked, "message"))


def decode_json(text: str) -> JsonValue:
    """Decode the ``data`` of an event. Raise MessageError for text that is not JSON."""
    try:
        value: JsonValue = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MessageError(f"the data is not JSON: {exc}") from None
    return value


def parse_session_event(event: str, data: str) -> SessionEvent:
    """Parse an event of a session stream. Each event of this stream must have ``session``."""
    if event not in SESSION_EVENTS:
        raise MessageError(f"the event type '{event}' is not an event of a session stream")
    parsed = parse_event(event, decode_json(data))
    if isinstance(parsed, LogEvent):
        raise MessageError("a log event is not an event of a session stream")
    if parsed.session is None:
        raise MessageError(f"{event}: an event of a session stream needs the field session")
    return parsed


def parse_broker_event(event: str, data: str) -> BrokerEvent:
    """Parse an event of the broker stream."""
    if event not in BROKER_EVENTS:
        raise MessageError(f"the event type '{event}' is not an event of the broker stream")
    parsed = parse_event(event, decode_json(data))
    if not isinstance(parsed, (NoticeEvent, LogEvent)):
        raise MessageError(f"the event type '{event}' is not an event of the broker stream")
    return parsed
