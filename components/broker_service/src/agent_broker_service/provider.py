"""The provider interface: the access to one kind of agent.

The configuration gives the provider of each agent. Each method gives checked JSON
messages of the schema. The events are events of the agent API: the field ``agent``
is optional. The event proxy adds it.

Errors: AgentError for an answer that passes to the client unchanged (``400``, ``404``,
``409``). AgentUnavailableError if the agent cannot be reached or gives a wrong answer.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from agent_broker_client.jsontypes import JsonObject, JsonValue

# One event of a stream: the SSE event type and the checked body.
type Event = tuple[str, JsonObject]


class UpstreamStream(ABC):
    """One open stream from an agent: a session stream or the agent stream."""

    @abstractmethod
    def events(self) -> AsyncIterator[Event]:
        """Give each event in order. End when the agent ends the stream.

        Raise AgentUnavailableError when the stream breaks.
        """

    @abstractmethod
    async def close(self) -> None:
        """Close the stream. The agent sees that the connection closes."""


class AgentProvider(ABC):
    """The operations of the broker API on one agent. ``agent`` is the name of the agent."""

    @property
    @abstractmethod
    def agent(self) -> str:
        """The name of the agent in the configuration."""

    async def open(self) -> None:
        """Prepare the provider at the start of the broker, e.g. read the workspace root. Default: nothing."""

    @abstractmethod
    async def close(self) -> None:
        """Close each connection of the provider."""

    @abstractmethod
    async def status(self) -> JsonObject:
        """``AgentStatus``."""

    @abstractmethod
    async def get_config(self) -> JsonObject:
        """``ConfigValues``."""

    @abstractmethod
    async def get_config_value(self, key: str) -> JsonObject:
        """``ConfigValue`` of a dotted key."""

    @abstractmethod
    async def set_config(self, key: str, value: JsonValue) -> JsonObject:
        """``ConfigSetResult``."""

    @abstractmethod
    async def list_models(self) -> JsonObject:
        """``ModelList``: the models of the agent and its DEFAULT_MODEL."""

    @abstractmethod
    async def list_efforts(self) -> JsonObject:
        """``EffortList``: the efforts of the agent and its DEFAULT_EFFORT."""

    @abstractmethod
    async def set_session_settings(self, session_id: str, request: JsonObject) -> JsonObject:
        """Change the model or the effort of a session. ``Session`` with the name of the agent in the broker."""

    @abstractmethod
    async def create_session(self, request: JsonObject) -> JsonObject:
        """Create a session from a checked ``SessionCreateRequest``. Give the ``Session``."""

    @abstractmethod
    async def list_sessions(self) -> list[JsonObject]:
        """Each ``Session`` of the agent, with no filter."""

    @abstractmethod
    async def get_session(self, session_id: str) -> JsonObject:
        """``Session`` with its current state."""

    @abstractmethod
    async def delete_session(self, session_id: str) -> None:
        """Delete the session at the agent."""

    @abstractmethod
    async def send_command(self, session_id: str, request: JsonObject) -> JsonObject:
        """Start a command from a checked ``CommandRequest``. Give ``CommandAccepted``."""

    @abstractmethod
    async def interrupt(self, session_id: str) -> JsonObject:
        """Stop the command that runs. Give ``SessionResult``."""

    @abstractmethod
    async def open_session_stream(self, session_id: str) -> UpstreamStream:
        """Open the session stream. The first event is the ``state`` of the session."""

    @abstractmethod
    async def open_agent_stream(self) -> UpstreamStream:
        """Open the agent stream: ``notice`` events of the kind ``lifecycle``, and ``log`` events."""
