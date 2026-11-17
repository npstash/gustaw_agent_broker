#!/usr/bin/env bash
# Launcher of the component client.
#   run.sh test [pytest args]   install the dev requirements and run the checks and the tests
#   run.sh cert [args]          make a new key pair for the broker (DESIGN.md, section 6.1.4)
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
# The bytecode caches of each Python of the runtime go into .local_assets/ (rule tests-and-ci).
"$venv/bin/python" -c 'import pathlib, sys, sysconfig; pathlib.Path(sysconfig.get_paths()["purelib"], "local-assets-pycache.pth").write_text(f"import sys; sys.pycache_prefix = {sys.argv[1]!r}\n")' "$dir/.local_assets/pycache"
if ! cmp -s requirements.txt "$venv/requirements.txt" 2>/dev/null || ! cmp -s pyproject.toml "$venv/pyproject.toml" 2>/dev/null; then
    "$venv/bin/python" -m pip install --quiet --upgrade pip
    "$venv/bin/python" -m pip install --quiet -r requirements.txt
    cp requirements.txt "$venv/requirements.txt"
    cp pyproject.toml "$venv/pyproject.toml"
fi
case "${1:-}" in
    test)
        shift
        "$venv/bin/python" -m pip install --quiet -r requirements-dev.txt
        exec "$venv/bin/python" -m pytest tests "$@"
        ;;
    cert)
        shift
        exec "$venv/bin/python" -m agent_broker_client.certtool "$@"
        ;;
    *)
        echo "usage: run.sh test | cert [--vault-home DIR] [--entry NAME] [--cert FILE] [--days N]" >&2
        exit 2
        ;;
esac
