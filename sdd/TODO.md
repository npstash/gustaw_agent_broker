# TODO — Agent Broker

Open work and open issues. Check an item when its work is committed. The steps are the steps of [ADR-20261115-service_split](the voice client repository/blob/main/sdd/adr/ADR-20261115-service_split.md) of voice client.

## Step 1: broker with the agent API

- [ ] 1a. Schema file `messages.schema.json` and its tests (DESIGN, section 8.2).
- [ ] 1b. Broker service: API, provider `agent_api`, event proxy, heartbeat, container control (DESIGN, sections 5.1, 6.1, 6.2).
- [ ] 1c. Client library: broker client and session client (DESIGN, sections 5.2, 6.4).
- [ ] 1d. Project files, `certtool`, new certificate with CERT_NAMES (DESIGN, sections 6.5, 8.3, 8.4).
- [ ] 1e. `compose.yaml` and `update.sh` (DESIGN, section 9).

## Step 6: OpenCode

- [ ] 6a. Provider `opencode` with the language detection (DESIGN, section 6.3).

## Open issues

The open issues are in [DESIGN.md](DESIGN.md), section 11.
