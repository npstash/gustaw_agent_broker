"""The message schema (DESIGN.md, section 8.2): the file, its references, and an example of each message."""
import copy
import uuid
from importlib import resources

import pytest
from jsonschema import Draft202012Validator

from agent_broker_client import messages, models
from agent_broker_client.jsontypes import JsonObject, JsonValue

CID = str(uuid.uuid4())
SESSION: JsonObject = {
    "id": "ses_1", "agent": "claude", "client": "voice", "workspace": "x", "workspace_path": "/repo/x",
    "state": "idle", "title": "Chat", "created_at": "2026-11-16T10:00:00Z", "last_modified": "2026-11-16T10:05:00Z",
}
AGENT: JsonObject = {
    "name": "claude", "provider": "agent_api", "container_state": "running", "agent_state": "ok", "version": "1.0",
}
SCOPE: JsonObject = {"agent": "claude", "session": "ses_1"}


def without(body: JsonObject, key: str) -> JsonObject:
    result = copy.deepcopy(body)
    del result[key]
    return result


def with_value(body: JsonObject, key: str, value: JsonValue) -> JsonObject:
    result = copy.deepcopy(body)
    result[key] = value
    return result


# Each message has valid examples and invalid examples.
VALID: dict[str, list[JsonValue]] = {
    "SessionState": ["idle", "working", "stopped"],
    "Language": ["en", "pl"],
    "ContainerState": ["running", "exited", "restarting", "paused", "created", "dead", "missing"],
    "AgentState": ["ok", "unreachable"],
    "UtcDateTime": ["2026-11-16T10:00:00Z", "2026-11-16T10:00:00.123456+00:00", "2016-12-31T23:59:60Z",
                    "2026-11-16T23:59:59.123456789Z"],
    "CommandRequest": [{"id": CID, "text": "hi"}, {"id": CID, "text": "hi", "mode": "voice"}],
    "CommandAccepted": [{"id": CID, "status": "accepted"}, {"id": CID, "status": "duplicate"}],
    "ConfigSetRequest": [{"value": 1}, {"value": None}],
    "ConfigValue": [{"key": "claude.model", "value": "opus"}],
    "ConfigValues": [{"values": []}, {"values": [{"key": "a.b", "value": [1, 2]}]}],
    "ConfigSetResult": [{"key": "a", "value": 1, "applied": "now"}, {"key": "a", "value": 1, "applied": "at_restart"}],
    "AgentList": [{"agents": []}, {"agents": [
        AGENT, with_value(AGENT, "provider", "opencode"),
        {**AGENT, "container_state": "missing", "agent_state": "unreachable"}]}],
    "AgentResult": [{"agent": "claude", "container_state": "running"}, {"agent": "claude", "container_state": "exited"}],
    "AgentStatus": [{"uptime_s": 0, "version": "1.0", "open_sessions": 2}],
    "SessionCreateRequest": [
        {"client": "voice", "workspace": "x"}, {"client": "voice", "workspace": ".", "force": True}],
    "Session": [SESSION, with_value(SESSION, "client", None), with_value(SESSION, "title", None)],
    "SessionList": [{"sessions": []}, {"sessions": [SESSION]}],
    "SessionResult": [{"session_state": "idle"}],
    "ErrorResponse": [
        {"error": error, "message": "text"} for error in (
            "bad_request", "unauthorized", "not_found", "busy", "workspace_not_found", "agent_unavailable")],
    "StateEvent": [{**SCOPE, "state": "working"}],
    "StatusEvent": [{**SCOPE, "kind": "activity", "text": "Reading a file"}],
    "ReplyEvent": [{**SCOPE, "command_id": CID, "message": "Done", "language": "en"}],
    "NoticeEvent": [
        {**SCOPE, "kind": "command_failed", "command_id": CID, "text": "Failed"},
        {**SCOPE, "kind": "command_dropped", "command_id": None, "text": "Dropped"},
        {**SCOPE, "kind": "lifecycle", "command_id": None, "text": "New chat"},
        {"agent": "claude", "kind": "lifecycle", "command_id": None, "text": "Container started"},
    ],
    "LogEvent": [{"agent": "claude", "component": "api", "level": "INFO", "time": "10:00:00", "message": "up"}],
}
INVALID: dict[str, list[JsonValue]] = {
    "SessionState": ["busy", ""],
    "Language": ["eng", "E1"],
    "ContainerState": ["stopped", "removing", ""],
    "AgentState": ["ready", "ok "],
    "UtcDateTime": ["2026-11-16T12:00:00+02:00", "2026-11-16 10:00Z", "yesterday", "2026-13-40T10:00:00Z", 1,
                    "2026-11-16T24:00:00Z", "2026-11-16T10:60:00Z", "2026-11-16T10:00:61Z", "2026-11-16T10:00:00Z\n",
                    "2026-02-30T10:00:00Z", "2026-11-16T10:00:00", "2026-11-16T10:00:00.Z", "20261116T100000Z"],
    "CommandRequest": [{"id": "not-a-uuid", "text": "hi"}, {"id": CID, "text": ""}, {"id": CID, "text": "x", "extra": 1}],
    "CommandAccepted": [{"id": CID, "status": "done"}, {"id": CID}],
    "ConfigSetRequest": [{}, {"value": 1, "extra": 1}],
    "ConfigValue": [{"key": "a"}, {"value": 1}],
    "ConfigValues": [{}, {"values": [{"key": "a"}]}],
    "ConfigSetResult": [{"key": "a", "value": 1, "applied": "later"}, {"key": "a", "value": 1}],
    "AgentList": [
        {}, {"agents": [without(AGENT, "version")]}, {"agents": [with_value(AGENT, "provider", "other")]},
        {"agents": [with_value(AGENT, "container_state", "up")]}, {"agents": [with_value(AGENT, "agent_state", "ready")]}],
    "AgentResult": [
        {"agent": "claude"}, {"agent": "claude", "container_state": 1}, {"container_state": "running"},
        {"agent": "claude", "container_state": "stopped"}],
    "AgentStatus": [{"uptime_s": -1, "version": "1.0", "open_sessions": 2}, {"uptime_s": 0, "version": "1.0"}],
    "SessionCreateRequest": [
        {"workspace": "x"}, {"client": "voice"}, {"client": "voice", "workspace": "x", "force": "yes"},
        {"client": "", "workspace": "x"}],
    "Session": [
        without(SESSION, "agent"), with_value(SESSION, "state", "busy"), with_value(SESSION, "client", 1),
        without(SESSION, "title"), with_value(SESSION, "created_at", "2026-11-16T11:00:00+01:00"),
        with_value(SESSION, "last_modified", "16.11.2026 10:05")],
    "SessionList": [{}, {"sessions": [without(SESSION, "id")]}],
    "SessionResult": [{}, {"session_state": "busy"}],
    "ErrorResponse": [{"error": "unknown", "message": "x"}, {"error": "busy"}],
    "StateEvent": [{"state": "idle"}, without({**SCOPE, "state": "idle"}, "session"), {**SCOPE, "state": "busy"}],
    "StatusEvent": [{"kind": "activity", "text": "x"}, {**SCOPE, "kind": "other", "text": "x"}],
    "ReplyEvent": [
        {"command_id": CID, "message": "Done", "language": "en"},
        {**SCOPE, "command_id": CID, "message": "Done"}],
    "NoticeEvent": [
        {"kind": "lifecycle", "command_id": None, "text": "x"},
        {"agent": "claude", "kind": "command_failed", "command_id": CID, "text": "x"},
        {**SCOPE, "kind": "other", "command_id": None, "text": "x"}],
    "LogEvent": [
        {"component": "api", "level": "INFO", "time": "10:00:00", "message": "up"},
        {"agent": "claude", "component": "api", "level": "LOUD", "time": "10:00:00", "message": "up"}],
}


def test_every_message_has_examples() -> None:
    names = set(messages.message_names())
    assert set(VALID) == names
    assert set(INVALID) == names
    assert all(VALID[n] and INVALID[n] for n in names)


@pytest.mark.parametrize("name", sorted(VALID))
def test_valid_examples(name: str) -> None:
    for example in VALID[name]:
        assert messages.validate(name, example) == example


@pytest.mark.parametrize("name", sorted(INVALID))
def test_invalid_examples(name: str) -> None:
    for example in INVALID[name]:
        with pytest.raises(messages.MessageError):
            messages.validate(name, example)


def test_the_changes_of_the_design() -> None:
    names = set(messages.message_names())
    assert {"SessionCreateRequest", "Session", "SessionList", "AgentList", "AgentResult", "AgentStatus"} <= names
    assert not names & {"RestartResult", "ServerStatus"}
    assert messages.schema()["$id"] == "agent_broker/messages"


def test_schema_is_a_valid_draft_2020_12_schema() -> None:
    Draft202012Validator.check_schema(messages.schema())
    assert messages.schema()["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def references(node: JsonValue) -> list[str]:
    """Each $ref value in a JSON value."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(references(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(references(item))
    return found


def test_each_reference_resolves() -> None:
    root = messages.schema()
    refs = references(root)
    assert refs
    for ref in refs:
        assert ref.startswith("#/"), ref
        node: JsonValue = root
        for part in ref[2:].split("/"):
            assert isinstance(node, dict) and part in node, f"{ref} does not resolve"
            node = node[part]


def test_events_map_to_messages() -> None:
    body: JsonObject = {**SCOPE, "state": "idle"}
    assert messages.validate_event("state", body) == body
    with pytest.raises(messages.MessageError):
        messages.validate_event("bogus", {})
    with pytest.raises(messages.MessageError):
        messages.validate_event("state", {"state": "idle"})   # a session event needs agent and session


def test_error_body() -> None:
    assert messages.error_body("agent_unavailable", "down") == {"error": "agent_unavailable", "message": "down"}
    with pytest.raises(messages.MessageError):
        messages.error_body("nothing", "x")


def test_unknown_message_name() -> None:
    with pytest.raises(KeyError):
        messages.validate("Nothing", {})


def test_schema_is_package_data() -> None:
    resource = resources.files("agent_broker_client").joinpath("messages.schema.json")
    assert resource.is_file()


def test_date_time_check_works_without_an_extra_package() -> None:
    assert messages.is_date_time("2026-11-16T10:00:00Z")
    assert messages.is_date_time("2026-11-16T10:00:00.5+00:00")
    assert messages.is_date_time("2026-11-16t10:00:00z")
    assert messages.is_date_time("2026-11-16T12:00:00+02:00")
    assert messages.is_date_time("2016-12-31T23:59:60Z")
    assert messages.is_date_time(1)   # the check applies only to a string
    for value in ["2026-11-16T25:00:00Z", "2026-11-16", "2026-11-16T24:00:00Z", "2026-11-16T10:00:00+00:60",
                  "2026-11-16T10:00:00+24:00", "2026-11-16T10:00:00Z\n", "\u0662\u0660\u0662\u0666-11-16T10:00:00Z"]:
        assert not messages.is_date_time(value), value


def test_leap_second_gives_a_session() -> None:
    session = models.parse_session(with_value(SESSION, "created_at", "2016-12-31T23:59:60.5Z"))
    assert session.created_at.isoformat() == "2016-12-31T23:59:59.500000+00:00"
