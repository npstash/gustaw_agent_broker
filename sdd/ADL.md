# Architecture Decision Log (ADL)

The log of the decisions that change the design. Write one line for each decision. The format is in the rule architecture-decision-log of maya-agent-kit.

The commit ID of each entry below is the last commit of voice client at the time of the decision. This repository did not exist then.

- 20261114-01 Nestor 40d86ef1d4e4 The Agent Broker is an independent service in a Docker container, with its own project and repository
- 20261114-02 Nestor 40d86ef1d4e4 The broker routes each call to the agent that the client selects and gives one API for all agents
- 20261114-03 Nestor 40d86ef1d4e4 The broker starts, stops and restarts the agent containers through the Docker socket
- 20261114-04 Nestor 40d86ef1d4e4 The agent API is the API and schema of the Claude server of voice client, moved to this project
- 20261114-05 Nestor 40d86ef1d4e4 The OpenCode adapter is a provider inside the broker
- 20261114-06 Nestor 40d86ef1d4e4 Each agent or adapter detects the language of a reply; the broker itself does not
- 20261114-07 Nestor 40d86ef1d4e4 Only the broker has TLS and a port on the LAN; the agents use connections without encryption on 127.0.0.1 with no authentication
- 20261114-08 Nestor 40d86ef1d4e4 The secrets of the broker are in the vault vault-agent_broker
- 20261114-09 Nestor 40d86ef1d4e4 A client project uses the schema and the client library from the submodule of this project, with no copy
- 20261114-10 Nestor 40d86ef1d4e4 The image holds the code; a host script pulls, builds and starts the container
- 20261114-11 Nestor 40d86ef1d4e4 The broker keeps no state on disk; tracking in memory is allowed
- 20261114-12 Nestor 40d86ef1d4e4 Each call names a session; the broker API creates, lists and deletes sessions
- 20261114-13 Nestor 40d86ef1d4e4 A session carries a client ID that the agent stores; the client ID is required at creation
- 20261114-14 Nestor 40d86ef1d4e4 The client code has a broker client that makes session clients; one process can hold several broker clients
- 20261114-15 Nestor 40d86ef1d4e4 Each session has its own event stream; one broker stream for all agents carries the lifecycle, log and broker events
- 20261114-16 Nestor 40d86ef1d4e4 The first event of a session stream is the current state of the session
- 20261114-17 Nestor 40d86ef1d4e4 At a restart of an agent the broker closes its session streams and the session client connects again
- 20261114-18 Nestor 40d86ef1d4e4 The session list of the agent API returns all sessions; the broker filters by the required client ID
- 20261114-19 Nestor 40d86ef1d4e4 The broker reads the state of an agent and of a session from Docker and the agent at each call, with no cache
- 20261114-20 Nestor 40d86ef1d4e4 The broker sends a heartbeat on each stream to a client; the agents send none
- 20261114-21 Nestor 40d86ef1d4e4 A session workspace is a required path relative to the workspace root of the agent; chats use the paths of the agent with no translation
- 20261114-22 Nestor 40d86ef1d4e4 Session creation fails for a workspace folder that does not exist, unless the client sends force
- 20261114-23 Nestor 40d86ef1d4e4 The broker reads the workspace root of OpenCode from Docker: the working directory of the container
- 20261114-24 Nestor 40d86ef1d4e4 All clients use one shared login of the broker
- 20261114-25 Nestor 40d86ef1d4e4 certtool and the certificate are in this repository; a new key is made directly in vault-agent_broker
- 20261114-26 Nestor 40d86ef1d4e4 The certificate has the names broker-host, localhost, 127.0.0.1, 192.0.2.10 and broker.example.net
- 20261115-01 Nestor 40d86ef1d4e4 The broker container uses the host network
- 20261115-02 Nestor 73761e8f9317 The session ID does not change during the life of a session, also when the agent starts a new chat inside it
- 20261115-03 Nestor 73761e8f9317 The reconnect interval is a value of the client library; the session client and the broker client wait it between two tries
- 20261117-01 Nestor ba1ac6c4bd5c The broker API defines the failure behaviour for all agents: command_failed for the command, then state idle or stopped; in stopped a new command tries the session again; the broker sends command_failed when an upstream session stream ends in the state working
- 20261117-02 Opus-5.5 ba1ac6c4bd5c A new command gets 409 busy only in the state working
- 20261117-03 Opus-5.5 ba1ac6c4bd5c An interrupt gives command_dropped and then state idle; with no running command it does nothing and answers the current state
- 20261117-04 Opus-5.5 ba1ac6c4bd5c A lifecycle notice on a session stream tells only about the session; command_dropped is a command that ended with no result
- 20261117-05 Opus-5.5 ba1ac6c4bd5c A workspace is the root or one folder directly under it; a nested path and a symbolic link give 400
- 20261117-06 Opus-5.5 ba1ac6c4bd5c An agent can lose a session that has no command yet when the agent restarts; its ID then gives 404
- 20261117-07 Opus-5.5 ba1ac6c4bd5c The provider opencode sends only the state and the reply replay from the session log, then the live events
- 20261117-08 Opus-5.5 f15d8f0daf47 Each agent has the configuration keys logging.levels.<component>; the level selects the log events of the component
- 20261117-09 Opus-5.5 f15d8f0daf47 A command text that starts with / is a slash command of the agent; an unknown slash command gets a reply that says so
- 20261120-01 Nestor cb09fc0815a3 The container runs as the user of update.sh, not as a fixed UID; HOME and the TLS scratch folder are tmpfs folders of that user
- 20261120-02 Nestor cb09fc0815a3 The local configuration is in the XDG configuration folder of that user below gustaw/agent_broker, not in the clone
- 20261120-03 Nestor cb09fc0815a3 update.sh writes the variables of compose.yaml into .env of the project folder; the operator values are in config.env of the configuration folder
