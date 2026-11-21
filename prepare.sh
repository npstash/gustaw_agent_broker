#!/usr/bin/env bash
# Prepare the deployment of the Agent Broker for the current user.
#   prepare.sh                     make the host folders, copy compose.yaml into the deploy folder, write .env beside it
#   prepare.sh --override FILE     also copy FILE as compose.override.yaml, and write the folders of the vault into .env
#   prepare.sh --dry-run ...       print the steps; change nothing
# Run it as the user that owns the service, not as root. It changes no folder or file that exists.
# The operator values are in config.env of the configuration folder, one KEY=VALUE in each line:
#   AGENT_BROKER_DOCKER_SOCKET   the path of the Docker socket on the host (default /var/run/docker.sock)
# A variable of the environment replaces the value of config.env.
set -euo pipefail

SERVICE="agent_broker"
APP_FOLDER="gustaw/agent_broker"
VAULT="vault-agent_broker"
MAYA="maya-agent-kit"
PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOOKUP="$PROJECT/components/client/src/agent_broker_client/dependencies.py"

fail() { echo "prepare: $*" >&2; exit 1; }

dry=0 override=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) dry=1; shift ;;
        --override) [[ $# -ge 2 ]] || fail "--override needs a file"; override="$2"; shift 2 ;;
        *) echo "usage: prepare.sh [--dry-run] [--override FILE]" >&2; exit 2 ;;
    esac
done
act() { if [[ $dry -eq 1 ]]; then echo "prepare: would run: $*"; else "$@"; fi; }
# The value of KEY in a file of KEY=VALUE lines. Empty if the file or the key does not exist.
config_value() { [[ -f "$2" ]] || return 0; sed -n "s/^$1=//p" "$2" | tail -n 1; }

[[ "$(id -u)" != "0" ]] || fail "do not run this script as root"
[[ -z "$override" || -f "$override" ]] || fail "the file $override does not exist"

HOST_UID="$(id -u)"
HOST_GID="$(id -g)"
HOST_HOME="${HOME:?HOME is not set}"
HOST_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOST_HOME/.config}"
DEPLOY_DIR="$HOST_HOME/containers/$SERVICE"
AGENT_BROKER_DOCKER_SOCKET="${AGENT_BROKER_DOCKER_SOCKET:-$(config_value AGENT_BROKER_DOCKER_SOCKET "$HOST_CONFIG_HOME/$APP_FOLDER/config.env")}"
AGENT_BROKER_DOCKER_SOCKET="${AGENT_BROKER_DOCKER_SOCKET:-/var/run/docker.sock}"
DOCKER_GID="$(stat -c %g "$AGENT_BROKER_DOCKER_SOCKET" 2>/dev/null)" || fail "the Docker socket $AGENT_BROKER_DOCKER_SOCKET is not found"
VARS="HOST_UID HOST_GID HOST_CONFIG_HOME DOCKER_GID AGENT_BROKER_DOCKER_SOCKET"

if [[ -n "$override" ]]; then
    VAULT_DIR="$(python3 "$LOOKUP" "$VAULT" "$PROJECT")" || fail "the vault $VAULT is not found"
    VAULT_TOOL_DIR="$(python3 "$LOOKUP" "$MAYA" "$PROJECT")/tools/auth" || fail "$MAYA is not found"
    VARS="$VARS VAULT_DIR VAULT_TOOL_DIR"
fi

act mkdir -p "$HOST_CONFIG_HOME/$APP_FOLDER" "$DEPLOY_DIR"
act cp "$PROJECT/compose.yaml" "$DEPLOY_DIR/compose.yaml"
if [[ -n "$override" ]]; then
    act cp "$override" "$DEPLOY_DIR/compose.override.yaml"
else
    act rm -f "$DEPLOY_DIR/compose.override.yaml"
fi
if [[ $dry -eq 1 ]]; then
    echo "prepare: would write $DEPLOY_DIR/.env:"
else
    {
        echo "# Written by prepare.sh. Do not edit: run prepare.sh again."
        for var in $VARS; do printf '%s=%s\n' "$var" "${!var}"; done
    } > "$DEPLOY_DIR/.env"
    echo "prepare: wrote $DEPLOY_DIR/.env:"
fi
for var in $VARS; do printf '  %s=%s\n' "$var" "${!var}"; done
