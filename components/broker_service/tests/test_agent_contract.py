"""The contract suite of the agent API against the fake agent of these tests: the fake is verified."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fake_agent import FakeAgent

from agent_broker_client.contract import CASES, run_contract


def test_fake_agent_passes_the_contract(tmp_path: Path) -> None:
    async def scenario() -> dict[str, str]:
        agent = FakeAgent()
        await agent.start()
        try:
            return await run_contract(agent.url)
        finally:
            await agent.stop()

    failed = asyncio.run(scenario())
    assert failed == {}, failed
    assert len(CASES) == 10
