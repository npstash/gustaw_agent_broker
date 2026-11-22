"""The interface of the client library.

``client.py`` has the implementation. A program makes an ``HttpBrokerClient`` and uses the
types of this module.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from types import TracebackType
from typing import Self

from .errors import BrokerError
from .jsontypes import JsonValue
from .models import (AgentInfo, AgentResult, BrokerEvent, CommandAccepted, CommandMode, ConfigSetResult,
                     ConfigValue, EffortList, InvalidEvent, ModelList, Session, SessionEvent, SessionResult,
                     ToolPolicy)
from .stream import ConnectionListener, ConnectionState


class SessionClient(ABC):
    """One session of one agent, with one session stream.

    The stream connects when the client is made. It connects again after a heartbeat
    timeout or a closed stream, until ``close()``. Closing the client does not delete
    the session.
    """

    @property
    @abstractmethod
    def agent(self) -> str:
        """The name of the agent."""

    @property
    @abstractmethod
    def session_id(self) -> str:
        """The ID of the session."""

    @property
    @abstractmethod
    def connection_state(self) -> ConnectionState:
        """The state of the session stream."""

    @property
    @abstractmethod
    def last_error(self) -> BrokerError | None:
        """The error of the last failed try to connect. None after a stream that ended normally."""

    @abstractmethod
    async def send_command(self, command_id: str | uuid.UUID, text: str,
                           mode: CommandMode = CommandMode.TEXT) -> CommandAccepted:
        """Start a command. Raise BusyError if a command runs. A repeated ID gives ``duplicate``."""

    @abstractmethod
    async def interrupt(self) -> SessionResult:
        """Stop the command that runs."""

    @abstractmethod
    async def set_settings(self, model: str | None = None, effort: str | None = None) -> Session:
        """Change the model or the effort of this session. Raise BusyError if a command runs."""

    @abstractmethod
    def events(self) -> AsyncIterator[SessionEvent | InvalidEvent]:
        """Give each event of the session stream. After each connect, the first event is ``state``.

        An event that breaks the schema is an InvalidEvent; the stream continues. The iterator
        ends after ``close()``. It raises SessionGoneError if the broker answers ``404``.
        It raises UnauthorizedError for a ``401`` and BrokerTlsError for a TLS verification
        failure. After these errors, the client does not connect again.

        Only one caller at a time reads the events. If the caller does not read, the client
        stops reading the stream when its queue is full.
        """

    @abstractmethod
    async def close(self) -> None:
        """Close the session stream. Stop the reconnect."""

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                        traceback: TracebackType | None) -> None:
        await self.close()


class BrokerClient(ABC):
    """One connection to one broker with one client ID.

    The broker stream connects at the first call of ``events()``. ``close()`` also
    closes each session client that this client made.
    """

    @property
    @abstractmethod
    def client_id(self) -> str:
        """The client ID of the sessions that this client makes."""

    @property
    @abstractmethod
    def connection_state(self) -> ConnectionState:
        """The state of the broker stream."""

    @property
    @abstractmethod
    def last_error(self) -> BrokerError | None:
        """The error of the last failed try to connect the broker stream."""

    @abstractmethod
    async def list_agents(self) -> list[AgentInfo]:
        """All agents with their container state and agent state."""

    @abstractmethod
    async def start_agent(self, agent: str) -> AgentResult:
        """Start the container of the agent. Wait AGENT_CONTROL_TIMEOUT for the answer."""

    @abstractmethod
    async def stop_agent(self, agent: str) -> AgentResult:
        """Stop the container of the agent. The broker closes each session stream of the agent."""

    @abstractmethod
    async def restart_agent(self, agent: str) -> AgentResult:
        """Restart the container of the agent. The broker closes each session stream of the agent."""

    @abstractmethod
    async def get_config(self, agent: str) -> list[ConfigValue]:
        """All configuration values of the agent."""

    @abstractmethod
    async def get_config_value(self, agent: str, key: str) -> ConfigValue:
        """One configuration value. ``key`` is a dotted key."""

    @abstractmethod
    async def set_config(self, agent: str, key: str, value: JsonValue) -> ConfigSetResult:
        """Set one configuration value."""

    @abstractmethod
    def events(self) -> AsyncIterator[BrokerEvent | InvalidEvent]:
        """Give each event of the broker stream. The iterator ends after ``close()``.

        It raises UnauthorizedError for a ``401`` and BrokerTlsError for a TLS verification
        failure. After these errors, the client does not connect again.
        """

    @abstractmethod
    async def list_sessions(self, agent: str) -> list[Session]:
        """The sessions of the agent that have the client ID of this client."""

    @abstractmethod
    async def list_models(self, agent: str) -> ModelList:
        """The models of the agent, and its DEFAULT_MODEL: the starting value of a client."""

    @abstractmethod
    async def list_efforts(self, agent: str) -> EffortList:
        """The efforts of the agent, and its DEFAULT_EFFORT."""

    @abstractmethod
    async def create_session(self, agent: str, workspace: str, model: str, effort: str | None = None,
                             tools: ToolPolicy | None = None, force: bool = False, *,
                             on_connection: ConnectionListener | None = None) -> SessionClient:
        """Create a session with a model, and return its session client.

        Without ``effort``: the DEFAULT_EFFORT of the agent. Without ``tools``: each tool of the agent.
        Raise WorkspaceNotFoundError if the folder does not exist and ``force`` is false.
        """

    @abstractmethod
    def session(self, agent: str, session_id: str, *,
                on_connection: ConnectionListener | None = None) -> SessionClient:
        """Return a session client for a known session ID. Call it in a running event loop."""

    @abstractmethod
    async def get_session(self, agent: str, session_id: str) -> Session:
        """Read one session with its current state."""

    @abstractmethod
    async def set_session_settings(self, agent: str, session_id: str, model: str | None = None,
                                   effort: str | None = None) -> Session:
        """Change the model or the effort of a session. Raise BusyError if a command runs."""

    @abstractmethod
    async def delete_session(self, agent: str, session_id: str) -> None:
        """Delete the session at the agent. The agent closes the session stream."""

    @abstractmethod
    async def close(self) -> None:
        """Close the broker stream, each session client of this client, and the connections."""

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                        traceback: TracebackType | None) -> None:
        await self.close()
