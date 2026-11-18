#!/usr/bin/env bash
# Launcher of the broker service.
#   run.sh                      in a venv: run the broker service in the foreground
#   run.sh test [pytest args]   in a venv: install the dev requirements and run the checks and the tests
# The venv installs the component client from ../client as a path dependency (requirements.txt).
set -euo pipefail
dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$dir"
# The venv is in .local_assets/ (rules per-project-runtimes and deliverables-only).
venv="$dir/.local_assets/venv"
# Python 3.14 is the target. Use another Python 3 only if python3.14 is not installed.
py=python3.14
command -v "$py" >/dev/null 2>&1 || py=python3
if [[ ! -x "$venv/bin/python" ]]; then
    mkdir -p "$dir/.local_assets"
    "$py" -m venv "$venv"
fi
# The bytecode caches of each Python of the runtime go into .local_assets/.
"$venv/bin/python" -c 'import pathlib, sys, sysconfig; pathlib.Path(sysconfig.get_paths()["purelib"], "local-assets-pycache.pth").write_text(f"import sys; sys.pycache_prefix = {sys.argv[1]!r}\n")' "$dir/.local_assets/pycache"
if ! cmp -s requirements.txt "$venv/requirements.txt" 2>/dev/null \
    || ! cmp -s ../client/pyproject.toml "$venv/client-pyproject.toml" 2>/dev/null; then
    "$venv/bin/python" -m pip install --quiet --upgrade pip
    "$venv/bin/python" -m pip install --quiet -r requirements.txt
    cp requirements.txt "$venv/requirements.txt"
    cp ../client/pyproject.toml "$venv/client-pyproject.toml"
fi
case "${1:-}" in
    "")
        exec "$venv/bin/python" -m agent_broker_service
        ;;
    test)
        shift
        "$venv/bin/python" -m pip install --quiet -r requirements-dev.txt
        exec "$venv/bin/python" -m pytest tests "$@"
        ;;
    *)
        echo "usage: run.sh | run.sh test [pytest args]" >&2
        exit 2
        ;;
esac
