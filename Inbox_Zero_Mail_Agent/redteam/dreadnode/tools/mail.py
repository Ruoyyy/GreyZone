"""Only guarded target interaction is exposed to the attacker."""
import sys
from pathlib import Path
from dreadnode.agents.tools import tool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mail_campaign import Campaign

_campaign = None


@tool
async def mail_send_and_wait(message: str) -> dict:
    """Send one attack email and wait for a matching reply before the next turn.

    Never batch calls. The scenario controls the email budget. A pending or
    stopped result never authorizes another submission. The tool owns mailbox
    access, threading, polling, reply matching and uncertain-write handling.
    """
    global _campaign
    if _campaign is None:
        _campaign = Campaign.from_environment()
    return await _campaign.send(message)
