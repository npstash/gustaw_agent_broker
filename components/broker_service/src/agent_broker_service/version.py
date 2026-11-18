"""The version of the broker service, for the line at start."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

COMMIT_SUBJECT_MAX = 80
# A Docker image has no git data. The build gives the commit in these environment variables.
# The names of the version fields.
ENV_COMMIT = "AGENT_BROKER_COMMIT"
ENV_DATE = "AGENT_BROKER_COMMIT_DATE"
ENV_SUBJECT = "AGENT_BROKER_COMMIT_SUBJECT"


def _git(folder: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(["git", *args], cwd=folder, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def version(folder: Path) -> str:
    """The commit ID (12 characters), the commit date and the commit subject. ``unknown`` for a missing part."""
    commit = os.environ.get(ENV_COMMIT, "").strip()[:12] or _git(folder, "rev-parse", "--short=12", "HEAD")
    date = os.environ.get(ENV_DATE, "").strip()
    subject = os.environ.get(ENV_SUBJECT, "").strip()
    if not date:
        text = _git(folder, "log", "-1", "--format=%cI%n%s")
        if text is not None:
            date, _, subject = text.partition("\n")
    parts = (commit or "unknown", date or "unknown", subject[:COMMIT_SUBJECT_MAX])
    return " ".join(part for part in parts if part)
