# broker_service

The broker service: a component of the project [Agent Broker](../../README.md). For the design, see [DESIGN.md](../../sdd/DESIGN.md), sections 5.1, 6.1.1, 6.1.2 and 8.

The provider `opencode` (section 6.2.1) needs the OpenCode password. Without it, the service skips an enabled agent with this provider and writes a WARNING. The default configuration disables the agent `opencode`.

## Contents

| Item | Use |
| --- | --- |
| `src/agent_broker_service/app.py` | Start and stop: `Broker` and `main`. |
| `src/agent_broker_service/api.py` | The HTTPS API: basic authentication, schema checks, error answers, the routes. |
| `src/agent_broker_service/router.py` | The agent of a URL, with its provider and its container. |
| `src/agent_broker_service/provider.py` | The provider interface. |
| `src/agent_broker_service/agent_api.py` | The provider `agent_api`: HTTP to an agent that implements the agent API. |
| `src/agent_broker_service/proxy.py` | The event proxy: session streams, agent streams, the broker stream. |
| `src/agent_broker_service/streams.py` | The client streams: send queue, heartbeat, close. |
| `src/agent_broker_service/docker.py` | Container control: the interface `ContainerEngine`, and `DockerEngine` with direct HTTP calls on the Docker socket. |
| `src/agent_broker_service/hostops.py` | Container control: `HostopsEngine` on the socket of the host operation service hostops. |
| `src/agent_broker_service/opencode.py` | The provider `opencode`: the HTTP API of OpenCode, also for the workspace folders. |
| `src/agent_broker_service/settings.py` | The configuration values and their checks. |
| `src/agent_broker_service/tls.py`, `logs.py`, `version.py`, `errors.py` | The TLS context, the log lines, the version line, the errors. |
| `config/config.yaml` | The default configuration ([DESIGN.md](../../sdd/DESIGN.md), sections 8.3 and 12). No secrets. |
| `Dockerfile` | The image of the service. `update.sh`, `prepare.sh` and `compose.yaml` in the project folder build and run it. |
| `requirements.txt` | The pinned dependencies. It installs [../client](../client/README.md) as a path dependency. |

## Run

- Service: `run.sh` with no verb. The service runs in the foreground until SIGTERM or SIGINT.
- At start, the service opens its secrets: from vaultkeeper if `credentials.vault` and `credentials.vaultkeeper` are set, from the vault if only `credentials.vault` is set, else from environment variables (see the README of the project, "Secrets").
- The certificate is `server.crt` of the configuration folder (`cert.file`). Make it with `../client/run.sh cert --config FILE`.
- The log lines go to the standard error output. The first line gives the version: commit, date and subject.
- In a Docker image, give the version in `AGENT_BROKER_COMMIT`, `AGENT_BROKER_COMMIT_DATE` and `AGENT_BROKER_COMMIT_SUBJECT`.

## Configure

- Change a value in `local.yaml` of `${XDG_CONFIG_HOME:-$HOME/.config}/gustaw/agent_broker/`. A local value replaces the default value of the same key.
- A local file cannot add a key. It cannot add an agent. It can change each value of the agents `claude` and `opencode`.
- Each agent has `enabled`. The default is `true` for `claude` and `false` for `opencode`. The service serves only an enabled agent. A call for a disabled agent gives `404`.
- `log.level` is the log level: `ERROR`, `WARNING`, `INFO` (default) or `DEBUG`.
- An unknown provider name or a value that is not correct stops the start with an error.
- `docker.hostops` is the socket of the host operation service hostops, e.g. `/run/gustaw/hostops.sock`. If it is set, the service controls the containers through hostops and uses no Docker socket.
- `docker.socket` is the path of the Docker socket, if `docker.hostops` is empty. The account of the service must have access to it.
- `credentials.vaultkeeper` is the socket of the daemon vaultkeeper. The local file gives the vault entry of each secret, e.g. `credentials.secrets.api_username.entry`. The project names no vault entry.

## Test

- `run.sh test`. The command runs the type check (mypy, strict) and the tests.
- The tests start the real broker with TLS and use the real client library. They use a fake agent (`tests/fake_agent.py`), a fake Docker engine on a Unix socket (`tests/fake_docker.py`), a fake hostops (`tests/fake_hostops.py`), a fake OpenCode (`tests/fake_opencode.py`), a fake vaultkeeper (`tests/test_credentials.py`) and fake secrets (`tests/rig.py`).
- The tests read no vault and use no real Docker socket and no real daemon. They write only into `.local_assets/`.
- The venv is in `.local_assets/venv`.
