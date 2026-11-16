# client

The client library, the message schema and the certificate tool: a component of the project [Agent Broker](../../README.md). For the design, see [DESIGN.md](../../sdd/DESIGN.md), sections 5.2, 6.5 and 8.2.

A client project installs this component with pip. The package is `agent_broker_client`.

## Contents

| Item | Use |
| --- | --- |
| `src/agent_broker_client/messages.schema.json` | The contract of all messages (JSON Schema, draft 2020-12). Package data. |
| `src/agent_broker_client/messages.py` | Check a message or an event against the schema. |
| `src/agent_broker_client/certtool.py` | Make a new key pair for the broker. |
| `src/agent_broker_client/config.py` | Read the YAML configuration. Keep the comments. |
| `src/agent_broker_client/vault.py` | Get secrets from the vault with the vault tool (`maya/tools/auth`). `FakeSecrets` for tests. |
| `src/agent_broker_client/dependencies.py` | Find `maya-agent-kit` and the vault. A copy of the reference code. Do not change it. |
| `src/agent_broker_client/jsontypes.py` | The types of a JSON value. |
| `config/config.yaml` | The configuration of certtool: the certificate file, CERT_NAMES, CERT_VALIDITY, the vault and the vault entry of the private key. |
| `tls/server.crt` | The committed certificate. The private key is in the vault. Not made yet. |

## Run

- Tests: `run.sh test`. The command runs the type check (mypy, strict) and the tests.
- New key pair: `run.sh cert`. The tool writes the certificate to `tls/server.crt` and the private key to the vault entry `Agent Broker TLS 01`. Then commit the certificate and the vault change, and restart the broker.
- The venv is in `.local_assets/venv`.
