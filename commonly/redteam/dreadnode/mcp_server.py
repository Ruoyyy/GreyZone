"""The Dreadnode attacker receives only this bounded public turn tool."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastmcp import FastMCP
from redteam.runtime import perform_turn

server = FastMCP("commonly-attacker")


@server.tool()
async def attack_turn(message: str) -> dict:
    """Send one alice message to Scout, await completion and report public feedback."""
    try:
        return await perform_turn(message)
    except Exception:
        return {"status": "invalid", "error": "Target turn failed; stop and inspect host artifacts."}


if __name__ == "__main__":
    server.run(transport="stdio", show_banner=False)
