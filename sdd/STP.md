# Software Test Plan (STP) — Agent Broker

The tests of the Agent Broker: the broker service and the client library. This project has no SRS: it has no functional test and no integration test.

## 1. Unit tests

| Test | Description | API |
| --- | --- | --- |
| `components/broker_service/tests/test_package.py::test_package_imports` | Checks that package imports. | Client library: the package |
| `components/broker_service/tests/test_package.py::test_default_configuration_follows_the_design` | Checks that default configuration follows the design. | Client library: the package |
| `components/broker_service/tests/test_package.py::test_certificate_values_have_one_source` | CERT_NAMES and CERT_VALIDITY are only in the configuration of the component client. | Client library: the package |
| `components/broker_service/tests/test_settings.py::test_settings_of_the_default_configuration` | Checks that settings of the default configuration. | Configuration of the broker service |
| `components/broker_service/tests/test_settings.py::test_local_file_replaces_values` | Checks that local file replaces values. | Configuration of the broker service |
| `components/broker_service/tests/test_settings.py::test_unknown_provider_is_a_start_error` | Checks that unknown provider is a start error. | Configuration of the broker service |
| `components/broker_service/tests/test_settings.py::test_opencode_agent_is_skipped_with_a_warning` | Checks that opencode agent is skipped with a warning. | Configuration of the broker service |
| `components/broker_service/tests/test_settings.py::test_credentials_match` | Checks that credentials match. | Configuration of the broker service |
| `components/broker_service/tests/test_settings.py::test_version_line` | Checks that version line. | Configuration of the broker service |
| `components/broker_service/tests/test_settings.py::test_local_file_can_change_each_value_of_the_two_agents` | Checks that local file can change each value of the two agents. | Configuration of the broker service |
| `components/broker_service/tests/test_settings.py::test_local_file_cannot_add_an_agent` | Checks that local file cannot add an agent. | Configuration of the broker service |
| `components/broker_service/tests/test_settings.py::test_enabled_must_be_true_or_false` | Checks that enabled must be true or false. | Configuration of the broker service |
| `components/broker_service/tests/test_settings.py::test_disabled_agent_is_not_in_the_router_and_gives_no_warning` | Checks that disabled agent is not in the router and gives no warning. | Configuration of the broker service |
| `components/broker_service/tests/test_settings.py::test_log_level_is_a_configuration_value` | Checks that log level is a configuration value. | Configuration of the broker service |
| `components/broker_service/tests/test_types.py::test_mypy_strict` | Checks that mypy strict. | All interfaces: static type check |
| `components/client/tests/test_common.py::test_config_get_set_keeps_comments` | Checks that config get set keeps comments. | Client library: configuration |
| `components/client/tests/test_common.py::test_config_validator_at_load` | Checks that config validator at load. | Client library: configuration |
| `components/client/tests/test_common.py::test_secret_ref` | Checks that secret ref. | Client library: vault |
| `components/client/tests/test_common.py::test_fake_secrets` | Checks that fake secrets. | Client library: vault |
| `components/client/tests/test_common.py::test_vault_find_gives_a_secret_error` | Checks that vault find gives a secret error. | Client library: vault |
| `components/client/tests/test_common.py::test_from_config_finds_the_vault_by_its_name` | Checks that from config finds the vault by its name. | Client library: vault |
| `components/client/tests/test_common.py::test_project_dependencies_file_finds_both_submodules` | Checks that project dependencies file finds both submodules. | Client library: dependencies |
| `components/client/tests/test_common.py::test_dependencies_is_a_copy_of_the_reference_code` | Checks that dependencies is a copy of the reference code. | Client library: dependencies |
| `components/client/tests/test_common.py::test_resolve_path` | Checks that resolve path. | Client library |
| `components/client/tests/test_messages.py::test_every_message_has_examples` | Checks that every message has examples. | Client library: the check of the messages (`messages.schema.json`) |
| `components/client/tests/test_messages.py::test_valid_examples` | Checks that valid examples. | Client library: the check of the messages (`messages.schema.json`) |
| `components/client/tests/test_messages.py::test_invalid_examples` | Checks that invalid examples. | Client library: the check of the messages (`messages.schema.json`) |
| `components/client/tests/test_messages.py::test_the_changes_of_the_design` | Checks that the changes of the design. | Client library: the check of the messages (`messages.schema.json`) |
| `components/client/tests/test_messages.py::test_schema_is_a_valid_draft_2020_12_schema` | Checks that schema is a valid draft 2020 12 schema. | Client library: the check of the messages (`messages.schema.json`) |
| `components/client/tests/test_messages.py::test_each_reference_resolves` | Checks that each reference resolves. | Client library: the check of the messages (`messages.schema.json`) |
| `components/client/tests/test_messages.py::test_events_map_to_messages` | Checks that events map to messages. | Client library: the check of the messages (`messages.schema.json`) |
| `components/client/tests/test_messages.py::test_agent_events_need_no_agent_and_broker_events_need_it` | An agent does not send agent; the broker adds it. | Client library: the check of the messages (`messages.schema.json`) |
| `components/client/tests/test_messages.py::test_error_body` | Checks that error body. | Client library: the check of the messages (`messages.schema.json`) |
| `components/client/tests/test_messages.py::test_unknown_message_name` | Checks that unknown message name. | Client library: the check of the messages (`messages.schema.json`) |
| `components/client/tests/test_messages.py::test_schema_is_package_data` | Checks that schema is package data. | Client library: the check of the messages (`messages.schema.json`) |
| `components/client/tests/test_messages.py::test_date_time_check_works_without_an_extra_package` | Checks that date time check works without an extra package. | Client library: the check of the messages (`messages.schema.json`) |
| `components/client/tests/test_messages.py::test_leap_second_gives_a_session` | Checks that leap second gives a session. | Client library: the check of the messages (`messages.schema.json`) |
| `components/client/tests/test_messages.py::test_summary_of_an_error_has_no_content_of_the_message` | A log line uses ``summary``: the text of the error can hold a value of the message. | Client library: the check of the messages (`messages.schema.json`) |
| `components/client/tests/test_messages.py::test_data_with_too_many_levels_is_a_message_error` | Checks that data with too many levels is a message error. | Client library: the check of the messages (`messages.schema.json`) |
| `components/client/tests/test_types.py::test_mypy_strict` | Checks that mypy strict. | All interfaces: static type check |

## 2. Component tests

| Test | Description | API |
| --- | --- | --- |
| `components/broker_service/tests/test_agent_contract.py::test_fake_agent_passes_the_contract` | Checks that fake agent passes the contract. | Agent API (section 6.1.2): the contract suite against the fake agent |
| `components/broker_service/tests/test_broker.py::test_agent_list_asks_docker_and_the_agent_at_each_call` | Checks that agent list asks docker and the agent at each call. | `GET /agents` |
| `components/broker_service/tests/test_broker.py::test_missing_container` | Checks that missing container. | Broker API (section 6.1.1) |
| `components/broker_service/tests/test_broker.py::test_docker_not_reachable_gives_503` | Checks that docker not reachable gives 503. | `GET /events` |
| `components/broker_service/tests/test_broker.py::test_start_stop_and_restart_close_the_session_streams` | Checks that start stop and restart close the session streams. | `POST /agents/{agent}/start`, `stop`, `restart` |
| `components/broker_service/tests/test_broker.py::test_restart_gives_lifecycle_events_on_the_broker_stream` | Checks that restart gives lifecycle events on the broker stream. | `POST /agents/{agent}/start`, `stop`, `restart` |
| `components/broker_service/tests/test_broker.py::test_stop_gives_one_lifecycle_event_for_the_end` | Docker sends "die" and "stop" for a stop. | `POST /agents/{agent}/start`, `stop`, `restart` |
| `components/broker_service/tests/test_broker.py::test_disabled_agent_is_not_served` | Checks that disabled agent is not served. | `GET /agents` |
| `components/broker_service/tests/test_broker.py::test_ended_container_gives_a_lifecycle_event` | Checks that ended container gives a lifecycle event. | `GET /events` |
| `components/broker_service/tests/test_broker.py::test_broker_stream_merges_the_agent_stream_and_the_reachability` | Checks that broker stream merges the agent stream and the reachability. | `GET /events` |
| `components/broker_service/tests/test_broker.py::test_config_operations` | Checks that config operations. | `GET /agents/{agent}/config`, `PUT /agents/{agent}/config/{key}` |
| `components/broker_service/tests/test_broker.py::test_session_operations` | Checks that session operations. | Broker API (section 6.1.1) |
| `components/broker_service/tests/test_broker.py::test_session_list_needs_the_client` | Checks that session list needs the client. | `GET /agents/{agent}/sessions` |
| `components/broker_service/tests/test_broker.py::test_workspace_errors_pass_through` | Checks that workspace errors pass through. | `POST /agents/{agent}/sessions` |
| `components/broker_service/tests/test_broker.py::test_commands_busy_duplicate_and_interrupt` | Checks that commands busy duplicate and interrupt. | `POST /agents/{agent}/sessions/{id}/interrupt` |
| `components/broker_service/tests/test_broker.py::test_bad_requests` | Checks that bad requests. | Broker API (section 6.1.1) |
| `components/broker_service/tests/test_broker.py::test_authentication` | Checks that authentication. | Each operation: TLS and authentication |
| `components/broker_service/tests/test_broker.py::test_agent_that_is_down_gives_503` | Checks that agent that is down gives 503. | Broker API (section 6.1.1) |
| `components/broker_service/tests/test_broker.py::test_answer_of_the_agent_that_breaks_the_schema_gives_503` | Checks that answer of the agent that breaks the schema gives 503. | Broker API (section 6.1.1) |
| `components/broker_service/tests/test_broker.py::test_session_stream_gives_state_first_then_the_events_in_order_with_agent` | Checks that session stream gives state first then the events in order with agent. | `GET /agents/{agent}/sessions/{id}/events` |
| `components/broker_service/tests/test_broker.py::test_event_of_the_agent_that_breaks_the_schema_is_not_passed` | Checks that event of the agent that breaks the schema is not passed. | `GET /agents/{agent}/sessions/{id}/events` |
| `components/broker_service/tests/test_broker.py::test_unknown_session_stream_is_gone` | Checks that unknown session stream is gone. | `GET /agents/{agent}/sessions/{id}/events` |
| `components/broker_service/tests/test_broker.py::test_two_clients_on_two_sessions_and_two_streams_on_one_session` | Checks that two clients on two sessions and two streams on one session. | `GET /agents/{agent}/sessions/{id}/events` |
| `components/broker_service/tests/test_broker.py::test_upstream_end_closes_the_client_stream_and_the_client_connects_again` | Checks that upstream end closes the client stream and the client connects again. | `GET /agents/{agent}/sessions/{id}/events` |
| `components/broker_service/tests/test_broker.py::test_upstream_end_while_working_gives_command_failed` | The upstream session stream ends in the state working. | `POST /agents/{agent}/sessions/{id}/commands` |
| `components/broker_service/tests/test_broker.py::test_open_command_is_forgotten_after_its_reply_and_after_delete` | Checks that open command is forgotten after its reply and after delete. | `DELETE /agents/{agent}/sessions/{id}` |
| `components/broker_service/tests/test_broker.py::test_client_that_goes_away_closes_the_upstream_stream` | Checks that client that goes away closes the upstream stream. | `GET /agents/{agent}/sessions/{id}/events` |
| `components/broker_service/tests/test_broker.py::test_heartbeat_lines_on_an_idle_stream` | Checks that heartbeat lines on an idle stream. | `GET /agents/{agent}/sessions/{id}/events` |
| `components/broker_service/tests/test_broker.py::test_full_send_queue_closes_the_stream` | A client that reads too slowly loses its stream. | `GET /agents/{agent}/sessions/{id}/events` |
| `components/broker_service/tests/test_broker.py::test_stop_closes_each_stream_and_leaves_no_task` | Checks that stop closes each stream and leaves no task. | `POST /agents/{agent}/start`, `stop`, `restart` |
| `components/broker_service/tests/test_broker.py::test_sigterm_stops_the_broker` | Checks that sigterm stops the broker. | `POST /agents/{agent}/start`, `stop`, `restart` |
| `components/broker_service/tests/test_broker.py::test_dot_segments_of_a_url_do_not_change_the_path_at_the_agent` | ``..`` as a session ID or a key must not reach another operation of the agent. | Broker API (section 6.1.1) |
| `components/broker_service/tests/test_broker.py::test_redirect_of_the_agent_is_not_followed` | Checks that redirect of the agent is not followed. | Broker API (section 6.1.1) |
| `components/broker_service/tests/test_broker.py::test_head_opens_no_stream` | Checks that head opens no stream. | `GET /agents/{agent}/sessions/{id}/events` |
| `components/broker_service/tests/test_broker.py::test_body_that_is_too_large_or_too_deep_gives_400` | Checks that body that is too large or too deep gives 400. | Broker API (section 6.1.1) |
| `components/broker_service/tests/test_broker.py::test_dropped_event_gives_a_log_line_without_the_payload` | The broker drops no event silently. | `GET /agents/{agent}/sessions/{id}/events` |
| `components/broker_service/tests/test_broker.py::test_event_with_too_many_levels_is_dropped_and_the_stream_continues` | Checks that event with too many levels is dropped and the stream continues. | `GET /agents/{agent}/sessions/{id}/events` |
| `components/broker_service/tests/test_broker.py::test_log_line_of_a_failed_login_has_no_line_break` | Checks that log line of a failed login has no line break. | Each operation: TLS and authentication |
| `components/broker_service/tests/test_broker.py::test_failure_of_the_pump_ends_the_client_stream` | A failure of the pump must not leave the client stream open with a dead upstream stream. | `GET /agents/{agent}/sessions/{id}/events` |
| `components/broker_service/tests/test_broker.py::test_full_queue_writes_the_number_of_lost_events` | The broker writes the number of lost events of a closed stream to its log. | `GET /agents/{agent}/sessions/{id}/events` |
| `components/broker_service/tests/test_broker.py::test_client_that_goes_away_writes_the_number_of_lost_events` | Checks that client that goes away writes the number of lost events. | `GET /agents/{agent}/sessions/{id}/events` |
| `components/broker_service/tests/test_update_script.py::test_script_syntax` | Checks that script syntax. | Command line: `update.sh` |
| `components/broker_service/tests/test_update_script.py::test_dry_run_gives_the_steps_in_order_and_runs_nothing` | Checks that dry run gives the steps in order and runs nothing. | Command line: `update.sh` |
| `components/broker_service/tests/test_update_script.py::test_dry_run_keeps_the_branch_and_pulls_the_current_one` | Checks that dry run keeps the branch and pulls the current one. | Command line: `update.sh` |
| `components/broker_service/tests/test_update_script.py::test_detached_head_stops` | Checks that detached head stops. | Command line: `update.sh` |
| `components/broker_service/tests/test_update_script.py::test_local_changes_stop` | Checks that local changes stop. | Command line: `update.sh` |
| `components/broker_service/tests/test_update_script.py::test_missing_dependency_stops_and_names_it` | Checks that missing dependency stops and names it. | Command line: `update.sh` |
| `components/broker_service/tests/test_update_script.py::test_dependency_as_a_nested_folder_works` | Checks that dependency as a nested folder works. | Command line: `update.sh` |
| `components/broker_service/tests/test_update_script.py::test_vault_path_for_compose_is_the_found_one` | Checks that vault path for compose is the found one. | Command line: `update.sh` |
| `components/broker_service/tests/test_update_script.py::test_missing_docker_socket_stops` | Checks that missing docker socket stops. | Command line: `update.sh` |
| `components/broker_service/tests/test_update_script.py::test_unknown_option_is_a_usage_error` | Checks that unknown option is a usage error. | Command line: `update.sh` |
| `components/broker_service/tests/test_update_script.py::test_real_run_pulls_builds_from_the_commit_and_starts` | Checks that real run pulls builds from the commit and starts. | Command line: `update.sh` |
| `components/broker_service/tests/test_update_script.py::test_real_run_stops_at_the_first_error` | Checks that real run stops at the first error. | Command line: `update.sh` |
| `components/client/tests/test_certtool.py::test_names_are_told_apart` | Checks that names are told apart. | Command line: `run.sh cert` |
| `components/client/tests/test_certtool.py::test_ipv6_name_is_an_address` | Checks that ipv6 name is an address. | Command line: `run.sh cert` |
| `components/client/tests/test_certtool.py::test_certificate_kind_and_extensions` | Checks that certificate kind and extensions. | Command line: `run.sh cert` |
| `components/client/tests/test_certtool.py::test_default_configuration_follows_the_design` | Checks that default configuration follows the design. | Command line: `run.sh cert` |
| `components/client/tests/test_certtool.py::test_main_uses_the_configured_names_and_validity` | Checks that main uses the configured names and validity. | Command line: `run.sh cert` |
| `components/client/tests/test_certtool.py::test_entry_option_replaces_the_configured_entry` | Checks that entry option replaces the configured entry. | Command line: `run.sh cert` |
| `components/client/tests/test_certtool.py::test_bad_names_stop_before_any_write` | Checks that bad names stop before any write. | Command line: `run.sh cert` |
| `components/client/tests/test_certtool.py::test_certtool_writes_key_to_a_temporary_vault` | The real vault tool with a vault in tmp_path (as voice client does). | Command line: `run.sh cert` |
| `components/client/tests/test_client.py::test_agent_operations` | Checks that agent operations. | Client library (section 6.1.3) |
| `components/client/tests/test_client.py::test_config_operations` | Checks that config operations. | Client library: broker client, configuration |
| `components/client/tests/test_client.py::test_session_operations` | Checks that session operations. | Client library: session client |
| `components/client/tests/test_client.py::test_invalid_input_is_not_sent` | Checks that invalid input is not sent. | Client library (section 6.1.3) |
| `components/client/tests/test_client.py::test_basic_authentication_is_sent` | Checks that basic authentication is sent. | Client library (section 6.1.3) |
| `components/client/tests/test_client.py::test_error_answers` | Checks that error answers. | Client library (section 6.1.3) |
| `components/client/tests/test_client.py::test_workspace_not_found` | Checks that workspace not found. | Client library (section 6.1.3) |
| `components/client/tests/test_client.py::test_busy_command` | Checks that busy command. | Client library: session client |
| `components/client/tests/test_client.py::test_wrong_login` | Checks that wrong login. | Client library (section 6.1.3) |
| `components/client/tests/test_client.py::test_answer_that_breaks_the_schema` | Checks that answer that breaks the schema. | Client library (section 6.1.3) |
| `components/client/tests/test_client.py::test_broker_not_reachable` | Checks that broker not reachable. | Client library (section 6.1.3) |
| `components/client/tests/test_client.py::test_tls_checks_the_host_name` | Checks that tls checks the host name. | Client library: the certificate of the broker |
| `components/client/tests/test_client.py::test_default_certificate_file_is_the_one_of_the_configuration` | Checks that default certificate file is the one of the configuration. | Client library: broker client, configuration |
| `components/client/tests/test_client.py::test_session_stream_gives_state_first_then_live_events` | Checks that session stream gives state first then live events. | Client library: session client and broker client, streams |
| `components/client/tests/test_client.py::test_broker_stream` | Checks that broker stream. | Client library: session client and broker client, streams |
| `components/client/tests/test_client.py::test_several_session_clients_and_two_broker_clients` | Checks that several session clients and two broker clients. | Client library: session client |
| `components/client/tests/test_client.py::test_heartbeat_timeout_connects_again_and_gives_a_new_state` | Checks that heartbeat timeout connects again and gives a new state. | Client library: session client and broker client, streams |
| `components/client/tests/test_client.py::test_heartbeat_comments_keep_the_stream` | Checks that heartbeat comments keep the stream. | Client library: session client and broker client, streams |
| `components/client/tests/test_client.py::test_closed_stream_connects_again_after_the_interval` | Checks that closed stream connects again after the interval. | Client library: session client and broker client, streams |
| `components/client/tests/test_client.py::test_unavailable_agent_is_tried_again` | Checks that unavailable agent is tried again. | Client library (section 6.1.3) |
| `components/client/tests/test_client.py::test_404_for_a_stream_is_not_retried` | Checks that 404 for a stream is not retried. | Client library: session client and broker client, streams |
| `components/client/tests/test_client.py::test_deleted_session_ends_the_stream` | Checks that deleted session ends the stream. | Client library: session client and broker client, streams |
| `components/client/tests/test_client.py::test_close_stops_the_reconnect` | Checks that close stops the reconnect. | Client library: session client and broker client, streams |
| `components/client/tests/test_client.py::test_event_that_breaks_the_schema_is_given_and_the_stream_continues` | Checks that event that breaks the schema is given and the stream continues. | Client library: session client and broker client, streams |
| `components/client/tests/test_client.py::test_change_of_session` | The new session client gets its state; then the program closes the old one. | Client library: session client |
| `components/client/tests/test_client.py::test_listener_error_does_not_stop_the_stream` | Checks that listener error does not stop the stream. | Client library: session client and broker client, streams |
| `components/client/tests/test_client.py::test_401_for_a_stream_is_not_retried` | Checks that 401 for a stream is not retried. | Client library: session client and broker client, streams |
| `components/client/tests/test_client.py::test_tls_verification_failure_for_a_stream_is_not_retried` | Checks that tls verification failure for a stream is not retried. | Client library: session client and broker client, streams |
| `components/client/tests/test_client.py::test_stream_to_a_broker_that_is_not_reachable_is_tried_again` | Checks that stream to a broker that is not reachable is tried again. | Client library: session client and broker client, streams |
| `components/client/tests/test_client.py::test_error_answer_of_a_stream_with_no_body_is_tried_again` | Checks that error answer of a stream with no body is tried again. | Client library: session client and broker client, streams |
| `components/client/tests/test_client.py::test_two_readers_of_the_events_end_after_close` | Checks that two readers of the events end after close. | Client library: session client and broker client, streams |
| `components/client/tests/test_client.py::test_full_queue_drops_the_connection_and_keeps_the_waiting_events` | At STREAM_QUEUE_LIMIT the client drops the connection and connects again only after the program has read the waiting events. | Client library: session client and broker client, streams |
| `components/client/tests/test_client.py::test_agent_control_has_its_own_timeout` | Start, stop and restart wait AGENT_CONTROL_TIMEOUT; each other request keeps its timeout. | Client library: broker client, agent control |
| `components/client/tests/test_client.py::test_close_does_not_swallow_the_cancellation_of_its_caller` | Checks that close does not swallow the cancellation of its caller. | Client library (section 6.1.3) |
| `components/client/tests/test_client.py::test_no_session_client_is_made_during_the_close_of_the_broker_client` | Checks that no session client is made during the close of the broker client. | Client library: session client |
| `components/client/tests/test_client.py::test_character_in_two_chunks` | Checks that character in two chunks. | Client library (section 6.1.3) |
| `components/client/tests/test_client.py::test_answers_that_are_not_correct` | Checks that answers that are not correct. | Client library (section 6.1.3) |
| `components/client/tests/test_client.py::test_sse_decoder` | Checks that sse decoder. | Client library (section 6.1.3) |
| `components/client/tests/test_client.py::test_sse_encode_and_decode` | Checks that sse encode and decode. | Client library (section 6.1.3) |
| `components/client/tests/test_client.py::test_unknown_event_type_of_a_session_stream` | Checks that unknown event type of a session stream. | Client library: session client and broker client, streams |
| `components/client/tests/test_client.py::test_enums_agree_with_the_schema` | Checks that enums agree with the schema. | Client library (section 6.1.3) |

## 3. Functional tests

None.

## 4. Integration tests

None.

## 5. QAS tests

None.

## 6. Manual tests

None.
