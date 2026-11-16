# broker_service

The broker service: a component of the project [Agent Broker](../../README.md). For the design, see [DESIGN.md](../../sdd/DESIGN.md), section 5.1.

Status: skeleton only. The package `agent_broker_service` has no code yet.

## Contents

| Item | Use |
| --- | --- |
| `src/agent_broker_service/` | The service package. |
| `config/config.yaml` | The default configuration ([DESIGN.md](../../sdd/DESIGN.md), sections 8.3 and 12). No secrets. |
| `requirements.txt` | The pinned dependencies. It installs [../client](../client/README.md). |

## Run

- Tests: `run.sh test`. The command runs the type check (mypy, strict) and the tests.
- The venv is in `.local_assets/venv`.
