"""The contract suite of the agent API: an agent runs it against its own code.

Each case sends requests to one agent and checks the answers against the schema and the rules
of the agent API. ``run_contract`` gives the names of the cases that failed, with the reason.
The agent must accept a new session in the workspace ``workspace`` with ``force``.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable

import aiohttp

from . import messages
from .jsontypes import JsonObject, JsonValue

Case = Callable[["Agent"], Awaitable[None]]


class ContractError(Exception):
    """An answer of the agent does not agree with the agent API."""


class Agent:
    """The agent under test at ``base_url``, e.g. ``http://127.0.0.1:38444``."""

    def __init__(self, http: aiohttp.ClientSession, base_url: str, workspace: str):
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.workspace = workspace

    async def call(self, method: str, path: str, body: JsonObject | None = None) -> tuple[int, JsonValue]:
        async with self.http.request(method, self.base_url + path, json=body) as answer:
            text = await answer.text()
            value: JsonValue = json.loads(text) if text else None
            return answer.status, value

    async def new_session(self) -> str:
        status, body = await self.call("POST", "/sessions", {"client": "contract", "workspace": self.workspace,
                                                              "force": True})
        expect(status == 201, f"POST /sessions gave {status}, not 201")
        session = checked("Session", body)
        expect(session.get("state") == "idle", f"a new session has the state {session.get('state')}")
        return str(session["id"])

    async def first_event(self, session_id: str) -> tuple[str, JsonObject]:
        async with self.http.get(f"{self.base_url}/sessions/{session_id}/events") as answer:
            expect(answer.status == 200, f"the session stream gave {answer.status}")
            event, data = "", ""
            async for raw in answer.content:
                line = raw.decode("utf-8").rstrip("\r\n")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data = line[5:].strip()
                elif line == "" and event:
                    value: JsonValue = json.loads(data)
                    body = checked(messages.AGENT_EVENT_SCHEMAS.get(event, event), value)
                    return event, body
        raise ContractError("the session stream ended before its first event")


def expect(condition: bool, text: str) -> None:
    if not condition:
        raise ContractError(text)


def checked(name: str, value: JsonValue) -> JsonObject:
    """The value as a message of the schema. Raise ContractError if it does not agree."""
    try:
        messages.validate(name, value)
    except messages.MessageError as exc:
        raise ContractError(f"{name}: {exc}") from None
    if not isinstance(value, dict):
        raise ContractError(f"{name}: not an object")
    return value


async def status(agent: Agent) -> None:
    code, body = await agent.call("GET", "/status")
    expect(code == 200, f"GET /status gave {code}")
    checked("AgentStatus", body)


async def session_create_list_read(agent: Agent) -> None:
    session_id = await agent.new_session()
    code, body = await agent.call("GET", "/sessions")
    expect(code == 200, f"GET /sessions gave {code}")
    listed = checked("SessionList", body)
    sessions = listed.get("sessions")
    expect(isinstance(sessions, list) and any(isinstance(s, dict) and s.get("id") == session_id for s in sessions),
           "GET /sessions does not list the new session")
    code, body = await agent.call("GET", f"/sessions/{session_id}")
    expect(code == 200, f"GET /sessions/{{id}} gave {code}")
    checked("Session", body)


async def nested_workspace(agent: Agent) -> None:
    code, body = await agent.call("POST", "/sessions", {"client": "contract", "workspace": "a/b", "force": True})
    expect(code == 400, f"a nested workspace gave {code}, not 400")
    checked("ErrorResponse", body)


async def stream_starts_with_state(agent: Agent) -> None:
    session_id = await agent.new_session()
    event, body = await agent.first_event(session_id)
    expect(event == "state", f"the first event is {event}, not state")
    expect("agent" not in body, "an event of the agent API has the field agent")


async def interrupt_with_no_command(agent: Agent) -> None:
    session_id = await agent.new_session()
    code, body = await agent.call("POST", f"/sessions/{session_id}/interrupt")
    expect(code == 200, f"an interrupt with no command gave {code}")
    result = checked("SessionResult", body)
    expect(result.get("session_state") == "idle", f"an interrupt with no command gave the state {result.get('session_state')}")


async def duplicate_command(agent: Agent) -> None:
    session_id = await agent.new_session()
    command: JsonObject = {"id": str(uuid.uuid4()), "text": "contract", "mode": "text"}
    code, body = await agent.call("POST", f"/sessions/{session_id}/commands", command)
    expect(code == 202, f"a command gave {code}, not 202")
    expect(checked("CommandAccepted", body).get("status") == "accepted", "a new command is not accepted")
    code, body = await agent.call("POST", f"/sessions/{session_id}/commands", command)
    expect(code == 202, f"the same command again gave {code}, not 202")
    expect(checked("CommandAccepted", body).get("status") == "duplicate", "the same command again is not a duplicate")


async def delete_session(agent: Agent) -> None:
    session_id = await agent.new_session()
    code, _ = await agent.call("DELETE", f"/sessions/{session_id}")
    expect(code == 204, f"DELETE gave {code}, not 204")
    code, body = await agent.call("GET", f"/sessions/{session_id}")
    expect(code == 404, f"a deleted session gave {code}, not 404")
    checked("ErrorResponse", body)


CASES: dict[str, Case] = {
    "status": status,
    "session_create_list_read": session_create_list_read,
    "nested_workspace": nested_workspace,
    "stream_starts_with_state": stream_starts_with_state,
    "interrupt_with_no_command": interrupt_with_no_command,
    "duplicate_command": duplicate_command,
    "delete_session": delete_session,
}


async def run_contract(base_url: str, workspace: str = "contract") -> dict[str, str]:
    """Run each case against the agent at ``base_url``. Give the failed cases: name -> reason."""
    failed: dict[str, str] = {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as http:
        agent = Agent(http, base_url, workspace)
        for name, case in CASES.items():
            try:
                await case(agent)
            except ContractError as exc:
                failed[name] = str(exc)
    return failed
