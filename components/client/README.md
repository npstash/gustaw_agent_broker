# client

The client library, the message schema and the certificate tool: a component of the project [Agent Broker](../../README.md). For the design, see [DESIGN.md](../../sdd/DESIGN.md), sections 5.2, 6.1.3, 6.1.4 and 8.2.

A client project installs this component with pip. The package is `agent_broker_client`.

## Contents

| Item | Use |
| --- | --- |
| `src/agent_broker_client/messages.schema.json` | The contract of all messages (JSON Schema, draft 2020-12). Package data. |
| `src/agent_broker_client/messages.py` | Check a message or an event against the schema: `validate_event` for the broker API, `validate_agent_event` for the agent API. |
| `src/agent_broker_client/api.py` | The interface: `BrokerClient` and `SessionClient`. |
| `src/agent_broker_client/client.py` | The implementation: `HttpBrokerClient` and `HttpSessionClient`. |
| `src/agent_broker_client/models.py` | The typed messages and events, and the enums of the schema. |
| `src/agent_broker_client/errors.py` | The errors of the broker API. |
| `src/agent_broker_client/stream.py` | The SSE stream with the heartbeat timeout and the reconnect. |
| `src/agent_broker_client/sse.py`, `tls.py` | SSE lines; the client TLS context. |
| `src/agent_broker_client/certtool.py` | Make a new key pair for the broker. |
| `src/agent_broker_client/config.py` | Read the YAML configuration. Keep the comments. |
| `src/agent_broker_client/vault.py` | Get secrets from the vault with the vault tool (`maya/tools/auth`). `FakeSecrets` for tests. |
| `src/agent_broker_client/dependencies.py` | Find `maya-agent-kit` and the vault. A copy of the reference code. Do not change it. |
| `src/agent_broker_client/jsontypes.py` | The types of a JSON value. |
| `config/config.yaml` | The configuration of certtool: the certificate file, CERT_NAMES, CERT_VALIDITY, the vault and the vault entry of the private key. |
| `tls/server.crt` | The committed certificate. The private key is in the vault. Not made yet. |

## Use the client library

Import all names from `agent_broker_client`. Get the login from the vault of your project. The library reads no vault.

```python
import uuid
from agent_broker_client import HttpBrokerClient, ReplyEvent, StateEvent, InvalidEvent, SessionGoneError

async def main(username: str, password: str) -> None:
    async with HttpBrokerClient("https://broker.example:38443", username, password, "voice") as broker:
        agents = await broker.list_agents()
        session = await broker.create_session("claude", "my_repo")   # or broker.session("claude", known_id)
        await session.send_command(uuid.uuid4(), "Run the tests")
        try:
            async for event in session.events():
                if isinstance(event, InvalidEvent):
                    print("bad event:", event.error)
                elif isinstance(event, ReplyEvent):
                    print(event.message)
        except SessionGoneError:
            print("the session is deleted")
```

- `HttpBrokerClient(url, username, password, client_id)` makes the broker client. One process can hold several.
- Optional parameters: `cert_file` (default `tls/server.crt`), `heartbeat_timeout` (45 s), `reconnect_interval` (5 s), `request_timeout` (15 s), `agent_control_timeout` (AGENT_CONTROL_TIMEOUT, 60 s), `on_connection`.
- Start, stop and restart of an agent wait `agent_control_timeout`. Each other request waits `request_timeout`.
- The session client connects its stream when you make it. After each connect, the first event is `state`.
- After a heartbeat timeout or a closed stream, the client connects again. `connection_state` and `on_connection` give the state of the connection.
- A `404` for a session stream ends `events()` with `SessionGoneError`. The client does not connect again.
- A `401` or a TLS verification failure ends `events()` of each stream with `UnauthorizedError` or `BrokerTlsError`. The connection state is `failed`. The client does not connect again.
- The client tries again after a `503` and when the broker is not reachable.
- Each stream holds STREAM_QUEUE_LIMIT (100) events that the program did not read. At this limit, the client drops the connection. It keeps the waiting events. `last_error` is `StreamQueueFullError`.
- After that, the client connects again by the reconnect rule, but only after the program has read the waiting events. The first event of the new connection is `state`.
- An event that breaks the schema comes as `InvalidEvent`. The stream continues.
- An error answer raises a subclass of `BrokerError`, e.g. `BusyError` or `WorkspaceNotFoundError`.
- The broker stream connects at the first call of `broker.events()`.
- `close()` stops the reconnect. `broker.close()` also closes each session client of the broker client.

Change of session ([DESIGN.md](../../sdd/DESIGN.md), section 7.3):

1. Make a new session client: `new = await broker.create_session(...)` or `new = broker.session(agent, session_id)`.
2. Read `new.events()`. The first event is the `state` of the new session.
3. Keep the old session client until it expects no more events, or until the timeout of your program.
4. Call `await old.close()`.

## Run

- Tests: `run.sh test`. The command runs the type check (mypy, strict) and the tests.
- New key pair: `run.sh cert`. The tool writes the certificate to `tls/server.crt` and the private key to the vault entry `Agent Broker TLS 01`. Then commit the certificate and the vault change, and restart the broker.
- The venv is in `.local_assets/venv`.
