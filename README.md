# Agent Broker

One API for several coding agents. A client selects an agent and a session in each call.

Status: not implemented. The skeleton, the message schema and `certtool` exist ([sdd/TODO.md](sdd/TODO.md)).

## SDD

The project follows the [SDD workflow](../../../maya/tools/sdd-templates/workflow.md).

- Design: [sdd/DESIGN.md](sdd/DESIGN.md)
- Decision log: [sdd/ADL.md](sdd/ADL.md)
- Open issues: [sdd/TODO.md](sdd/TODO.md)

## Components

| Component | Contents |
| --- | --- |
| [components/broker_service](components/broker_service/README.md) | The broker service. Package `agent_broker_service`. |
| [components/client](components/client/README.md) | The client library, `messages.schema.json`, the certificate and `certtool`. Package `agent_broker_client`. |

A client project installs only `components/client` with pip.

## Run

- Each component has `run.sh`. See the README of the component.
- CI: [.github/workflows/tests.yml](.github/workflows/tests.yml) runs `run.sh test` in each component.

Agent Broker is a submodule of the repository `the workspace`. It needs the repositories `maya-agent-kit` and `vault-agent_broker`. [dependencies.txt](dependencies.txt) gives their layouts.
