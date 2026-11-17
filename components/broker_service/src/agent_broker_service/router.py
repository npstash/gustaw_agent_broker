"""The router: the agent that a URL names, with its provider and its container (DESIGN.md, section 5.1)."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from agent_broker_client.config import ConfigError
from agent_broker_client.models import Provider

from . import logs
from .agent_api import AgentApiProvider
from .provider import AgentProvider
from .settings import AgentSettings, Settings

log = logs.get("router")

type ProviderFactory = Callable[[AgentSettings, Settings], AgentProvider]


def _agent_api(agent: AgentSettings, settings: Settings) -> AgentProvider:
    return AgentApiProvider(agent.name, agent.url, settings.agent_request_timeout)


# The implemented providers. The provider opencode (DESIGN.md, section 6.2.1) is not implemented yet.
FACTORIES: dict[Provider, ProviderFactory] = {Provider.AGENT_API: _agent_api}


@dataclass(frozen=True)
class AgentEntry:
    """One agent: its name, the name of its provider, its container and the provider object."""
    name: str
    provider_name: Provider
    container: str
    provider: AgentProvider


class Router:
    def __init__(self, entries: Iterable[AgentEntry]):
        self._entries = {entry.name: entry for entry in entries}

    def get(self, name: str) -> AgentEntry | None:
        return self._entries.get(name)

    @property
    def entries(self) -> list[AgentEntry]:
        return list(self._entries.values())


def build(settings: Settings, factories: Mapping[Provider, ProviderFactory] = FACTORIES) -> Router:
    """Make the router of the configured agents.

    A disabled agent is not served: it is not in the router, so a call for it gives 404.
    An unknown provider name is a start error (ConfigError). An agent with a known provider
    that is not implemented is skipped with a WARNING, so that the other agents stay usable.
    """
    entries: list[AgentEntry] = []
    for agent in settings.agents:
        if not agent.enabled:
            log.info("The agent %s is disabled.", agent.name)
            continue
        try:
            provider = Provider(agent.provider)
        except ValueError:
            raise ConfigError(f"the agent {agent.name} has the unknown provider {agent.provider}") from None
        factory = factories.get(provider)
        if factory is None:
            log.warning("The provider %s is not implemented. The agent %s is not available.", provider.value, agent.name)
            continue
        entries.append(AgentEntry(agent.name, provider, agent.container, factory(agent, settings)))
    return Router(entries)
