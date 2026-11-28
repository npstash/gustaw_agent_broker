#!/usr/bin/env bash
# Prepare the deployment of the Agent Broker.
#   prepare.sh                     make the host folders, copy compose.yaml into the deploy folder, write .env beside it
#   prepare.sh --override FILE     also copy FILE as compose.override.yaml, and write the folders of the vault into .env
#   prepare.sh --dry-run ...       print the steps; change nothing
# Run it as the user that deploys, not as root. The deploy folder is in the home of this user.
# Service user: SERVICE_USER of config/service.env in the folder of FILE. The process then runs as the service user,
# and the host folders are the XDG folders of the service user. sudo -u makes its folders and files.
# Without a service user, the process runs as the user that deploys, with the XDG folders of this user.
# Grants files: hostops-grants.yaml and vaultkeeper-grants.yaml of the project, and each .override.yaml of the folder
# of FILE. The script replaces each ${NAME} with its value in .env and writes them into the deploy folder, mode 644.
# Container control: with a hostops grants file, the broker uses the host operation service hostops, and the
# container gets no Docker socket. Else the script copies compose.docker-socket.yaml into the deploy folder, adds it
# to COMPOSE_FILE of .env, and finds the GID of the Docker socket.
# The script changes no host folder or file that exists. It replaces the files of the deploy folder.
# The operator values are in config.env of the configuration folder, one KEY=VALUE in each line:
#   AGENT_BROKER_DOCKER_SOCKET   the path of the Docker socket on the host (default /var/run/docker.sock); not used with hostops
# A variable of the environment replaces the value of config.env.
set -euo pipefail

SERVICE="agent_broker"
APP_FOLDER="gustaw/agent_broker"
# The vault of the service, and the repository of the vault tool, as in dependencies.txt.
VAULT="vault-agent_broker"
MAYA="maya-agent-kit"
# The daemons of the host that read a grants file of the deploy folder.
DAEMONS="hostops vaultkeeper"
# The compose file of the Docker socket, for a host without hostops.
DOCKER_SOCKET_COMPOSE="compose.docker-socket.yaml"
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
# Run a command that changes something. In a dry run, print it.
act() { if [[ $dry -eq 1 ]]; then echo "prepare: would run: $*"; else "$@"; fi; }

[[ "$(id -u)" != "0" ]] || fail "do not run this script as root"
id -nG | tr ' ' '\n' | grep -qx docker || fail "the user $(id -un) is not in the group docker"
[[ -z "$override" || -f "$override" ]] || fail "the file $override does not exist"
override_dir=""
[[ -z "$override" ]] || override_dir="$(cd "$(dirname "$override")" && pwd)"
# The value of KEY in a file of KEY=VALUE lines. Empty if the file or the key does not exist.
config_value() { [[ -f "$2" ]] || return 0; sed -n "s/^$1=//p" "$2" | tail -n 1; }

HOST_HOME="${HOME:?HOME is not set}"
DEPLOY_DIR="$HOST_HOME/containers/$SERVICE"
SERVICE_USER=""
[[ -z "$override_dir" ]] || SERVICE_USER="$(config_value SERVICE_USER "$override_dir/config/service.env")"
if [[ -n "$SERVICE_USER" ]]; then
    [[ "$SERVICE_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || fail "SERVICE_USER is not a valid user name: $SERVICE_USER"
    passwd_entry="$(getent passwd "$SERVICE_USER")" || fail "the service user $SERVICE_USER does not exist"
    IFS=: read -r _ _ HOST_UID HOST_GID _ service_home _ <<< "$passwd_entry"
    [[ "$HOST_UID" != "0" && "$HOST_GID" != "0" ]] || fail "the service user $SERVICE_USER has the UID 0 or the GID 0"
    sudo -n -u "$SERVICE_USER" true 2>/dev/null || fail "the user $(id -un) cannot run commands as $SERVICE_USER with sudo -n"
    HOST_CONFIG_HOME="$service_home/.config"
    echo "prepare: service user $SERVICE_USER ($HOST_UID:$HOST_GID)"
else
    HOST_UID="$(id -u)"
    HOST_GID="$(id -g)"
    HOST_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOST_HOME/.config}"
fi
# Change a host folder or file of the process user: the service user, else the user that deploys.
as_owner() { if [[ -n "$SERVICE_USER" ]]; then act sudo -n -u "$SERVICE_USER" "$@"; else act "$@"; fi; }
# Read a host folder or file of the process user. Also in a dry run.
read_owner() { if [[ -n "$SERVICE_USER" ]]; then sudo -n -u "$SERVICE_USER" "$@"; else "$@"; fi; }
VARS="HOST_UID HOST_GID HOST_CONFIG_HOME"

# The container control: hostops if a hostops grants file exists, else the Docker socket.
hostops=0
[[ -f "$PROJECT/hostops-grants.yaml" ]] && hostops=1
[[ -n "$override_dir" && -f "$override_dir/hostops-grants.override.yaml" ]] && hostops=1
if [[ $hostops -eq 1 ]]; then
    echo "prepare: container control: hostops; no Docker socket"
else
    CONFIG_ENV="$HOST_CONFIG_HOME/$APP_FOLDER/config.env"
    if [[ -z "${AGENT_BROKER_DOCKER_SOCKET:-}" ]] && read_owner test -f "$CONFIG_ENV"; then
        AGENT_BROKER_DOCKER_SOCKET="$(read_owner cat "$CONFIG_ENV" | sed -n 's/^AGENT_BROKER_DOCKER_SOCKET=//p' | tail -n 1)"
    fi
    AGENT_BROKER_DOCKER_SOCKET="${AGENT_BROKER_DOCKER_SOCKET:-/var/run/docker.sock}"
    DOCKER_GID="$(stat -c %g "$AGENT_BROKER_DOCKER_SOCKET" 2>/dev/null)" || fail "the Docker socket $AGENT_BROKER_DOCKER_SOCKET is not found"
    COMPOSE_FILE="$DEPLOY_DIR/compose.yaml:$DEPLOY_DIR/$DOCKER_SOCKET_COMPOSE"
    [[ -z "$override" ]] || COMPOSE_FILE="$COMPOSE_FILE:$DEPLOY_DIR/compose.override.yaml"
    VARS="$VARS DOCKER_GID AGENT_BROKER_DOCKER_SOCKET COMPOSE_FILE"
    echo "prepare: container control: the Docker socket"
fi

if [[ -n "$override" ]]; then
    VAULT_DIR="$(python3 "$LOOKUP" "$VAULT" "$PROJECT")" || fail "the vault $VAULT is not found"
    VAULT_TOOL_DIR="$(python3 "$LOOKUP" "$MAYA" "$PROJECT")/tools/auth" || fail "$MAYA is not found"
    VARS="$VARS VAULT_DIR VAULT_TOOL_DIR"
fi

as_owner mkdir -p "$HOST_CONFIG_HOME/$APP_FOLDER"
act mkdir -p "$DEPLOY_DIR"

act cp "$PROJECT/compose.yaml" "$DEPLOY_DIR/compose.yaml"
if [[ $hostops -eq 1 ]]; then
    [[ ! -e "$DEPLOY_DIR/$DOCKER_SOCKET_COMPOSE" ]] || act rm -f "$DEPLOY_DIR/$DOCKER_SOCKET_COMPOSE"
else
    act cp "$PROJECT/$DOCKER_SOCKET_COMPOSE" "$DEPLOY_DIR/$DOCKER_SOCKET_COMPOSE"
fi
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

# Write SOURCE to TARGET with each ${NAME} replaced by its value in .env. Stop on a name without a value.
write_grants() {
    local source="$1" target="$2" text name pattern value
    text="$(cat "$source")"
    for name in $({ grep -o '\${[A-Za-z_][A-Za-z0-9_]*}' "$source" || true; } | sed 's/^\${//; s/}$//' | sort -u); do
        [[ " $VARS " == *" $name "* && -n "${!name}" ]] || fail "$source: \${$name} has no value in .env"
        pattern="\${$name}"
        value="${!name}"
        text="${text//"$pattern"/"$value"}"
    done
    if [[ $dry -eq 1 ]]; then
        echo "prepare: would write $target from $source, mode 644"
        return 0
    fi
    printf '%s\n' "$text" > "$target.prepare.tmp"
    chmod 644 "$target.prepare.tmp"
    mv -f "$target.prepare.tmp" "$target"
    echo "prepare: wrote $target from $source"
}
# A grants file without a source gives no grants: remove the deployed copy.
for daemon in $DAEMONS; do
    for name in "$daemon-grants.yaml" "$daemon-grants.override.yaml"; do
        if [[ "$name" == *.override.yaml ]]; then source="${override_dir:+$override_dir/$name}"; else source="$PROJECT/$name"; fi
        if [[ -n "$source" && -f "$source" ]]; then
            write_grants "$source" "$DEPLOY_DIR/$name"
        elif [[ -e "$DEPLOY_DIR/$name" ]]; then
            act rm -f "$DEPLOY_DIR/$name"
        fi
    done
done
