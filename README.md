# Agent Broker

One API for several coding agents. A client selects an agent and a session in each call.

Status: the broker with the agent API is implemented. It runs on the target host, with the agent `claude` behind it. The provider `opencode` is open ([sdd/TODO.md](sdd/TODO.md)).

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

## Deployment

The broker runs as one Docker container on the host. For the design, see [sdd/DESIGN.md](sdd/DESIGN.md), section 9.

Layout of the clones on the host. Put the dependencies next to the project, or inside it. The paths are in [dependencies.txt](dependencies.txt).

```
<folder>/agent_broker/            this project (its own clone)
<folder>/maya-agent-kit/          or <folder>/maya
<folder>/vault-agent_broker/
```

First start:

1. Clone the three repositories as shown.
2. Enrol the clone of the vault. Use the vault tool of maya-agent-kit: [tools/auth/README.md](../../../maya/tools/auth/README.md), section "Enrolment". The key of the clone must open the vault without a terminal.
3. Run `./update.sh`.

Update: run `./update.sh`. The script pulls the current branch of the clone. It does not switch the branch. It stops on a detached HEAD, on local changes, and when a dependency is missing. It builds the image from the committed code and runs `docker compose up -d`. `./update.sh --dry-run` prints the steps and changes nothing.

Local configuration: write `local.yaml` into `${XDG_CONFIG_HOME:-$HOME/.config}/gustaw/agent_broker/`. Restart the container to apply it. `config.env` in the same folder gives the operator values of `update.sh` (`AGENT_BROKER_DOCKER_SOCKET`). `update.sh` writes the variables of `compose.yaml` into `.env` of the project folder.

Verified in the target environment (Ubuntu Server 26.04 LTS; 2026-11-18, commit `2cf1715`): the image build, the start of the container, the tests of each component, the login and the certificate. A session at the agent `claude` gave a reply to a command and was deleted. Not verified: the agent `opencode`.

Agent Broker is a submodule of the repository `the workspace`. It needs the repositories `maya-agent-kit` and `vault-agent_broker`. [dependencies.txt](dependencies.txt) gives their layouts.
