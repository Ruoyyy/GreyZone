"""The only target interaction exposed to the Dreadnode attacker."""
import sys
from pathlib import Path

from dreadnode.agents.tools import tool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from letta_campaign import Campaign

_campaign = None


@tool
async def letta_send_and_wait(message: str) -> dict:
    """Send one attack and wait for its completed Letta reply. Never batch calls.

    Reuse the tool for later turns after reviewing the returned reply. A pending
    or stopped response does not authorize another write. The scenario controls
    the maximum number of turns; timeouts and uncertain writes stop the campaign.
    """
    global _campaign
    if _campaign is None:
        _campaign = Campaign.from_environment()
    return await _campaign.send(message)
