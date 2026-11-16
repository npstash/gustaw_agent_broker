"""Keep the temporary folders of pytest in the project (rule nothing-outside-repo).

pytest makes --basetemp without its parent folder. .local_assets/ is gitignored, so a
new clone has no such folder. Make the parent folder before the first test.
"""
from pathlib import Path

import pytest

BASETEMP: Path = Path(__file__).resolve().parent / ".local_assets" / "pytest"


def pytest_configure(config: pytest.Config) -> None:
    if config.option.basetemp:
        return
    BASETEMP.parent.mkdir(parents=True, exist_ok=True)
    config.option.basetemp = str(BASETEMP)
