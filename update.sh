#!/usr/bin/env bash
# Update the Agent Broker on the host. Run it in the clone of the project.
#   update.sh              pull the current branch, build the image from the committed code, run the container
#   update.sh --dry-run    print the steps; run no git command and no Docker command that changes something
# The script works on the repository that it is in. It does not switch the branch.
# It never prints a secret. It stops at the first error with a non-zero exit code.
# The container runs as the user of this script. The script writes the variables of compose.yaml into .env.
# The operator values are in config.env of the configuration folder (CONFIG_DIR below), one KEY=VALUE in each line:
#   AGENT_BROKER_DOCKER_SOCKET   the path of the Docker socket on the host (default /var/run/docker.sock)
# A variable of the environment replaces the value of config.env.
set -euo pipefail

# The names of the dependencies in dependencies.txt that this script needs.
MAYA="maya-agent-kit"
VAULT="vault-agent_broker"

main() {
    local dry=0 project lookup step=0
    case "${1:-}" in
        "") ;;
        --dry-run) dry=1 ;;
        *) echo "usage: update.sh [--dry-run]" >&2; return 2 ;;
    esac
    project="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    lookup="$project/components/client/src/agent_broker_client/dependencies.py"

    note() { step=$((step + 1)); printf 'update: step %d: %s\n' "$step" "$*"; }
    fail() { printf 'update: error: %s\n' "$*" >&2; exit 1; }
    # Run a command that changes something. In a dry run, print it.
    act() {
        if [[ $dry -eq 1 ]]; then printf 'update: would run: %s\n' "$*"; else "$@"; fi
    }
    git_() { git -C "$project" "$@"; }

    # 1. The branch and the work tree.
    note "check the clone $project"
    local branch
    branch="$(git_ symbolic-ref --quiet --short HEAD)" || fail "HEAD is detached. Check out a branch, then run the script again."
    if [[ -n "$(git_ status --porcelain --untracked-files=no)" ]]; then
        fail "the clone has local changes. Commit or discard them, then run the script again."
    fi
    printf 'update: branch %s\n' "$branch"

    # 2. Pull the current branch. The branch does not change.
    note "pull the branch $branch"
    act git -C "$project" pull --ff-only

    # 3. The dependencies: submodules or separate clones, found with the lookup of dependencies.txt.
    note "check the dependencies"
    local names name folder out maya_dir="" vault_dir=""
    [[ -f "$project/dependencies.txt" ]] || fail "$project/dependencies.txt is missing"
    names="$(sed -n 's/^\[\(.*\)\]$/\1/p' "$project/dependencies.txt")"
    for name in "$MAYA" "$VAULT"; do
        grep -qxF "$name" <<<"$names" || fail "dependencies.txt has no section [$name]"
    done
    for name in $names; do
        if ! out="$(python3 "$lookup" "$name" "$project" 2>&1)"; then
            printf 'update: error: the dependency %s is missing. Clone it next to the project, or as a submodule.\n' "$name" >&2
            printf '%s\n' "$out" >&2
            exit 1
        fi
        folder="$out"
        printf 'update: dependency %s: %s\n' "$name" "$folder"
        if [[ "$name" == "$MAYA" ]]; then maya_dir="$folder"; fi
        if [[ "$name" == "$VAULT" ]]; then vault_dir="$folder"; fi
    done

    # The variables of compose.yaml. The user of this script is the user of the container.
    [[ "$(id -u)" != "0" ]] || fail "do not run this script as root"
    local config_dir="${XDG_CONFIG_HOME:-${HOME:?HOME is not set}/.config}/gustaw/agent_broker"
    local socket docker_gid
    socket="${AGENT_BROKER_DOCKER_SOCKET:-$(config_value AGENT_BROKER_DOCKER_SOCKET "$config_dir/config.env")}"
    socket="${socket:-/var/run/docker.sock}"
    docker_gid="$(stat -c %g "$socket")" || fail "the Docker socket $socket is not found"
    export AGENT_BROKER_VAULT="$vault_dir" DOCKER_GID="$docker_gid" AGENT_BROKER_DOCKER_SOCKET="$socket" \
        AGENT_BROKER_CONFIG_DIR="$config_dir" HOST_UID HOST_GID
    HOST_UID="$(id -u)"
    HOST_GID="$(id -g)"
    local var
    for var in HOST_UID HOST_GID DOCKER_GID AGENT_BROKER_DOCKER_SOCKET AGENT_BROKER_VAULT AGENT_BROKER_CONFIG_DIR; do
        printf 'update: compose variable %s=%s\n' "$var" "${!var}"
    done

    # 4. Build the image from the committed code, not from the work tree.
    note "build the image from the commit $(git_ rev-parse --short=12 HEAD)"
    local context="$project/.local_assets/build" layout="workspace/projects/gustaw/gustaw_agent_broker"
    act rm -rf "$context"
    act mkdir -p "$context/$layout" "$context/workspace/maya"
    if [[ $dry -eq 1 ]]; then
        printf 'update: would run: git archive HEAD of the project into %s\n' "$context/$layout"
        printf 'update: would run: git archive HEAD tools/auth of %s into %s\n' "$maya_dir" "$context/workspace/maya"
    else
        git_ archive HEAD | tar -x -C "$context/$layout"
        git -C "$maya_dir" archive HEAD tools/auth | tar -x -C "$context/workspace/maya"
        # One fixed time for all files: Docker then reuses the layers of files that did not change.
        find "$context" -exec touch -h -d @0 {} +
    fi
    # The folder of the optional local configuration (mount of compose.yaml). Make it here: Docker would make it as root.
    act mkdir -p "$AGENT_BROKER_CONFIG_DIR"
    # Plain docker compose commands in the project folder read the same values from .env.
    if [[ $dry -eq 1 ]]; then
        printf 'update: would write %s\n' "$project/.env"
    else
        {
            echo "# Written by update.sh. Do not edit: run update.sh again."
            for var in HOST_UID HOST_GID DOCKER_GID AGENT_BROKER_DOCKER_SOCKET AGENT_BROKER_VAULT AGENT_BROKER_CONFIG_DIR; do
                printf '%s=%s\n' "$var" "${!var}"
            done
        } > "$project/.env"
    fi
    export AGENT_BROKER_COMMIT AGENT_BROKER_COMMIT_DATE AGENT_BROKER_COMMIT_SUBJECT
    AGENT_BROKER_COMMIT="$(git_ rev-parse --short=12 HEAD)"
    AGENT_BROKER_COMMIT_DATE="$(git_ log -1 --format=%cI)"
    AGENT_BROKER_COMMIT_SUBJECT="$(git_ log -1 --format=%s)"
    act docker compose --project-directory "$project" -f "$project/compose.yaml" build

    # 5. Create the container again from the new image.
    note "run the container"
    act docker compose --project-directory "$project" -f "$project/compose.yaml" up -d
    note "done"
}

# The value of KEY in a file of KEY=VALUE lines. Empty if the file or the key does not exist.
config_value() {
    [[ -f "$2" ]] || return 0
    sed -n "s/^$1=//p" "$2" | tail -n 1
}

# The body is in a function, so that bash has read the whole script before the pull changes the file.
main "$@"
exit
