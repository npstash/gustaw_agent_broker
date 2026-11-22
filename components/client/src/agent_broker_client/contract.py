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

    async def default_model(self) -> str:
        """The DEFAULT_MODEL of the agent, from GET /models."""
        status, body = await self.call("GET", "/models")
        expect(status == 200, f"GET /models gave {status}")
        return str(checked("ModelList", body)["default"])

    async def new_session(self, extra: JsonObject | None = None) -> JsonObject:
        request: JsonObject = {"client": "contract", "workspace": self.workspace, "force": True,
                               "model": await self.default_model()}
        status, body = await self.call("POST", "/sessions", {**request, **(extra or {})})
        expect(status == 201, f"POST /sessions gave {status}, not 201")
        session = checked("Session", body)
        expect(session.get("state") == "idle", f"a new session has the state {session.get('state')}")
        return session

    async def new_session_id(self) -> str:
        return str((await self.new_session())["id"])

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
    session_id = await agent.new_session_id()
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
    code, body = await agent.call("POST", "/sessions", {"client": "contract", "workspace": "a/b", "force": True,
                                                        "model": await agent.default_model()})
    expect(code == 400, f"a nested workspace gave {code}, not 400")
    checked("ErrorResponse", body)


async def stream_starts_with_state(agent: Agent) -> None:
    session_id = await agent.new_session_id()
    event, body = await agent.first_event(session_id)
    expect(event == "state", f"the first event is {event}, not state")
    expect("agent" not in body, "an event of the agent API has the field agent")


async def interrupt_with_no_command(agent: Agent) -> None:
    session_id = await agent.new_session_id()
    code, body = await agent.call("POST", f"/sessions/{session_id}/interrupt")
    expect(code == 200, f"an interrupt with no command gave {code}")
    result = checked("SessionResult", body)
    expect(result.get("session_state") == "idle", f"an interrupt with no command gave the state {result.get('session_state')}")


async def duplicate_command(agent: Agent) -> None:
    session_id = await agent.new_session_id()
    command: JsonObject = {"id": str(uuid.uuid4()), "text": "contract", "mode": "text"}
    code, body = await agent.call("POST", f"/sessions/{session_id}/commands", command)
    expect(code == 202, f"a command gave {code}, not 202")
    expect(checked("CommandAccepted", body).get("status") == "accepted", "a new command is not accepted")
    code, body = await agent.call("POST", f"/sessions/{session_id}/commands", command)
    expect(code == 202, f"the same command again gave {code}, not 202")
    expect(checked("CommandAccepted", body).get("status") == "duplicate", "the same command again is not a duplicate")


async def delete_session(agent: Agent) -> None:
    session_id = await agent.new_session_id()
    code, _ = await agent.call("DELETE", f"/sessions/{session_id}")
    expect(code == 204, f"DELETE gave {code}, not 204")
    code, body = await agent.call("GET", f"/sessions/{session_id}")
    expect(code == 404, f"a deleted session gave {code}, not 404")
    checked("ErrorResponse", body)


async def models_and_efforts(agent: Agent) -> None:
    code, body = await agent.call("GET", "/models")
    expect(code == 200, f"GET /models gave {code}")
    models = checked("ModelList", body)
    ids = [m.get("id") for m in models["models"] if isinstance(m, dict)] if isinstance(models["models"], list) else []
    expect(models["default"] in ids, "the default model is not in the list of models")
    code, body = await agent.call("GET", "/efforts")
    expect(code == 200, f"GET /efforts gave {code}")
    efforts = checked("EffortList", body)
    expect(isinstance(efforts["efforts"], list) and efforts["default"] in efforts["efforts"],
           "the default effort is not in the list of efforts")


async def session_settings(agent: Agent) -> None:
    code, body = await agent.call("GET", "/efforts")
    efforts = checked("EffortList", body)
    session = await agent.new_session()
    expect(session.get("model") == await agent.default_model(), "the session does not have the requested model")
    expect(session.get("effort") == efforts["default"], "a session with no effort does not have the default effort")
    expect(session.get("tools") == {"allow": None, "deny": []}, "a session with no tools does not have each tool")
    code, body = await agent.call("PUT", f"/sessions/{session['id']}/settings", {"effort": efforts["default"]})
    expect(code == 200, f"PUT /sessions/{{id}}/settings gave {code}")
    checked("Session", body)
    code, body = await agent.call("PUT", f"/sessions/{session['id']}/settings", {"effort": "no-such-effort"})
    expect(code == 400, f"an unknown effort gave {code}, not 400")
    expect(checked("ErrorResponse", body).get("error") == "invalid_setting", "an unknown effort is not invalid_setting")


async def model_is_required(agent: Agent) -> None:
    code, body = await agent.call("POST", "/sessions", {"client": "contract", "workspace": agent.workspace,
                                                        "force": True})
    expect(code == 400, f"a session with no model gave {code}, not 400")
    checked("ErrorResponse", body)


CASES: dict[str, Case] = {
    "models_and_efforts": models_and_efforts,
    "session_settings": session_settings,
    "model_is_required": model_is_required,
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
