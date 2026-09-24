"""Bounded polling delay for asynchronous mail replies."""

import asyncio
import os

from dreadnode.agents.tools import tool


@tool
async def mail_poll_pause() -> str:
    """Wait for the scenario's polling interval before checking customer mail again."""
    seconds = int(os.environ.get("MAIL_POLL_INTERVAL_SECONDS", "5"))
    await asyncio.sleep(seconds)
    return f"{seconds} seconds elapsed; check for the pending mail reply again."
