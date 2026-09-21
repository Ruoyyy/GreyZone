"""Bounded polling delay; no access to the target or host resources."""

import asyncio

# 2.0.46's top-level lazy export points at a missing dreadnode.core.tools.
from dreadnode.agents.tools import tool


@tool
async def letta_poll_pause() -> str:
    """Wait 15 seconds before checking the pending Letta reply again."""
    await asyncio.sleep(15)
    return "15 seconds elapsed; read the pending issue's comments again."
