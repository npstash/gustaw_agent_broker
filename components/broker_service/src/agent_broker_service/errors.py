"""The errors of the parts of the broker service.

The api module makes an error answer from each of these errors.
"""
from __future__ import annotations

from agent_broker_client.jsontypes import JsonObject


class AgentError(Exception):
    """An agent gave an error answer that the broker passes to the client unchanged: ``400``, ``404`` or ``409``.

    ``body`` is a checked ``ErrorResponse``.
    """

    def __init__(self, status: int, body: JsonObject):
        super().__init__(f"{status} {body.get('error')}: {body.get('message')}")
        self.status = status
        self.body = body


class AgentUnavailableError(Exception):
    """The broker cannot reach the agent, or the answer of the agent is not correct. The answer is ``503``."""


class DockerError(Exception):
    """A call on the Docker socket failed."""


class DockerUnavailableError(DockerError):
    """The Docker engine did not answer, or it gave an unexpected answer. The answer is ``503``."""


class ContainerMissingError(DockerError):
    """No container with the name exists. The answer is ``404``."""


class NotFoundError(Exception):
    """The URL names an agent or a path that does not exist. The answer is ``404``."""


class BadRequestError(Exception):
    """The request of the client is not correct. The answer is ``400``."""
