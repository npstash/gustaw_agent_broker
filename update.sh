#!/usr/bin/env bash
# Update the Agent Broker on the host. Run it in the clone of the project.
#   update.sh [--override FILE]    pull the current branch, build the image from the committed code, run prepare.sh, start the container
#   update.sh --no-up ...          do all steps except "docker compose up -d"
#   update.sh --pull ...           build with the newest base image of the tag; no effect for a base image with a digest
#   update.sh --dry-run ...        print the steps; run no git command and no Docker command that changes something
# The script does not switch the branch. It never prints a secret. It stops at the first error with a non-zero exit code.
# prepare.sh gives the operator values of config.env.
set -euo pipefail

SERVICE="agent_broker"

main() {
    local dry=0 up=1 pull=0 project step=0
    local -a prepare_args=()
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run) dry=1; prepare_args+=("--dry-run"); shift ;;
            --no-up) up=0; shift ;;
            --pull) pull=1; shift ;;
            --override) [[ $# -ge 2 ]] || { echo "update: --override needs a file" >&2; return 2; }
                prepare_args+=("--override" "$(realpath "$2")"); shift 2 ;;
            *) echo "usage: update.sh [--dry-run] [--no-up] [--pull] [--override FILE]" >&2; return 2 ;;
        esac
    done
    project="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    note() { step=$((step + 1)); printf 'update: step %d: %s\n' "$step" "$*"; }
    fail() { printf 'update: error: %s\n' "$*" >&2; exit 1; }
    # Run a command that changes something. In a dry run, print it.
    act() { if [[ $dry -eq 1 ]]; then printf 'update: would run: %s\n' "$*"; else "$@"; fi; }
    git_() { git -C "$project" "$@"; }

    # 1. The branch and the work tree.
    note "check the clone $project"
    [[ "$(id -u)" != "0" ]] || fail "do not run this script as root"
    local branch
    branch="$(git_ symbolic-ref --quiet --short HEAD)" || fail "HEAD is detached. Check out a branch, then run the script again."
    if [[ -n "$(git_ status --porcelain --untracked-files=no)" ]]; then
        fail "the clone has local changes. Commit or discard them, then run the script again."
    fi
    printf 'update: branch %s\n' "$branch"

    # 2. Pull the current branch. The branch does not change.
    note "pull the branch $branch"
    act git -C "$project" pull --ff-only

    # 3. Build the image from the committed code, not from the work tree.
    local commit context="$project/.local_assets/build"
    commit="$(git_ rev-parse --short=12 HEAD)"
    note "build the image $SERVICE:$commit from the committed code"
    act rm -rf "$context"
    act mkdir -p "$context/opt/gustaw/agent_broker"
    if [[ $dry -eq 1 ]]; then
        printf 'update: would run: git archive HEAD of the project into %s\n' "$context/opt/gustaw/agent_broker"
    else
        git_ archive HEAD | tar -x -C "$context/opt/gustaw/agent_broker"
        # One fixed time for all files: Docker then reuses the layers of files that did not change.
        find "$context" -exec touch -h -d @0 {} +
    fi
    local -a build_args=()
    [[ $pull -eq 0 ]] || build_args+=("--pull")
    if [[ $pull -eq 1 ]] && git_ show HEAD:components/broker_service/Dockerfile | grep -qE '^FROM[[:space:]]+[^[:space:]]+@sha256:'; then
        printf 'update: warning: the Dockerfile pins the base image with a digest: --pull has no effect\n' >&2
    fi
    act docker build "${build_args[@]}" -t "$SERVICE:$commit" -t "$SERVICE:latest" \
        --build-arg "AGENT_BROKER_COMMIT=$commit" \
        --build-arg "AGENT_BROKER_COMMIT_DATE=$(git_ log -1 --format=%cI)" \
        --build-arg "AGENT_BROKER_COMMIT_SUBJECT=$(git_ log -1 --format=%s)" \
        -f "$context/opt/gustaw/agent_broker/components/broker_service/Dockerfile" "$context"

    # 4. The host folders, the deploy folder and .env.
    note "prepare the deploy folder"
    "$project/prepare.sh" "${prepare_args[@]}" || fail "prepare.sh failed"

    # 5. Create the container again from the new image.
    if [[ $up -eq 0 ]]; then
        note "skip docker compose up -d (--no-up)"
        return 0
    fi
    note "run the container"
    act docker compose --project-directory "$HOME/containers/$SERVICE" up -d
    note "done"
}

# The body is in a function, so that bash has read the whole script before the pull changes the file.
main "$@"
exit
