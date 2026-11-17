# TODO — Agent Broker

Open work and open issues. Check an item when its work is committed. The steps are the steps of [ADR-20261115-service_split](the voice client repository/blob/main/sdd/adr/ADR-20261115-service_split.md) of voice client.

## Step 1: broker with the agent API

- [x] 1a. Schema file `messages.schema.json` and its tests (DESIGN, section 8.2).
- [x] 1b. Broker service: API, provider `agent_api`, event proxy, heartbeat, container control (DESIGN, sections 5.1, 6.1.1, 6.1.2).
- [x] 1c. Client library: broker client and session client (DESIGN, sections 5.2, 6.1.3).
- [x] 1d. Project files, `certtool`, new certificate with CERT_NAMES (DESIGN, sections 6.1.4, 8.3, 8.4).
- [x] 1e. `compose.yaml` and `update.sh` (DESIGN, section 9).

## Step 7: OpenCode

- [ ] 6a. Provider `opencode` with the language detection (DESIGN, section 6.2.1).

## Open issues

The open issues are in [DESIGN.md](DESIGN.md), section 11.

## Tests of dependencies that are not direct

- Find each test of this project that tests a dependency that is not direct. Remove it. A project tests only its own code and its direct dependencies.

## Session states and failures (DESIGN, section 6.1.1)

- Broker: send `command_failed` for the open command when an upstream session stream ends in the state `working`.
- Provider `opencode` (step 7): follow the session states and the failures; send only the state and the reply replay from the session log.
- Provider `opencode` (step 7): the keys `logging.levels.<component>` and the `log` events; the reply to an unknown slash command.
