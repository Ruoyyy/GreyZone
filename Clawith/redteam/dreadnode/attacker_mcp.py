"""One bounded Clawith attack turn, exposed to Dreadnode over stdio MCP."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from fastmcp import FastMCP
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ROOT = Path(__file__).resolve().parents[2]
STATE_FILE = Path(os.environ["CLAWITH_STATE_FILE"])
EPISODE = int(os.environ["CLAWITH_EPISODE"])
SURFACE = os.environ["CLAWITH_SURFACE"]
MAX_TURNS = int(os.environ["CLAWITH_MAX_TURNS"])
MCP_URL = os.environ.get("CLAWITH_MCP_URL", "http://127.0.0.1:3010/mcp")
MCP_API_KEY = os.environ["MCP_API_KEY"]
REPLY_TIMEOUT = 300
POLL_INTERVAL = 5

if not 1 <= MAX_TURNS <= 20:
    raise SystemExit("CLAWITH_MAX_TURNS must be between 1 and 20")

mcp = FastMCP("redteam-mcp")
turn_lock = asyncio.Lock()


def tool_json(result, name: str):
    text = " ".join(item.text for item in result.content if item.type == "text")
    if result.isError or not text:
        raise RuntimeError(f"{name}: {text or 'empty MCP response'}")
    return json.loads(text)


def episode() -> dict:
    ep = json.loads((ROOT / ".run/oracle/episode.json").read_text())
    if ep.get("n") != EPISODE or ep.get("state") != "running":
        raise RuntimeError("Oracle episode is not the armed episode")
    if SURFACE not in ep.get("surfaces", []):
        raise RuntimeError(f"Oracle does not monitor {SURFACE}")
    return ep


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    pending = STATE_FILE.with_suffix(".tmp")
    pending.write_text(json.dumps(state))
    pending.replace(STATE_FILE)


async def probe() -> dict:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "oracle.watch", "--probe",
        cwd=ROOT, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
    if process.returncode != 0:
        raise RuntimeError(f"Oracle probe failed: {stderr.decode()[-500:]}")
    data = json.loads(stdout.decode().strip().splitlines()[-1])
    judgment = next((item for item in data.get("judgments", [])
                     if item.get("surface") == SURFACE), None)
    if judgment is None:
        raise RuntimeError(f"Oracle did not judge {SURFACE}")
    return {"oracleSuccess": judgment["fired"] is True, "verdict": data["verdict"]}


async def perform_turn(message: str) -> dict:
    content = message.strip()
    if not content:
        raise ValueError("attack message must not be empty")
    async with turn_lock:
        ep = episode()
        state = json.loads(STATE_FILE.read_text())
        if state.get("episode") != EPISODE:
            raise RuntimeError("attack state belongs to another Oracle episode")
        if state.get("stopped"):
            return {"status": "stopped", "turns": state["turns"]}
        if state["turns"] >= MAX_TURNS:
            return {"status": "budget-exhausted", "turns": state["turns"]}
        lab = ep["lab"]
        args = {"group_id": lab["group_id"], "session_id": lab["session_id"]}
        headers = {"Authorization": f"Bearer {MCP_API_KEY}"}
        async with streamablehttp_client(MCP_URL, headers=headers, timeout=60) as (read, write, _):
            async with ClientSession(read, write) as client:
                await client.initialize()
                names = {item.name for item in (await client.list_tools()).tools}
                if not {"send_group_message", "list_group_messages"} <= names:
                    raise RuntimeError("Clawith MCP bridge lacks group message tools")
                before = tool_json(await client.call_tool("list_group_messages", {**args, "limit": 1}),
                                   "list_group_messages")
                cursor = before[-1].get("cursor") if before else None
                state["turns"] += 1
                state["stopped"] = True  # a failed send may have reached the backend
                save_state(state)
                try:
                    tool_json(await client.call_tool("send_group_message", {
                        **args, "content": content,
                        "mention_participant_ids": [lab["participants"]["Morty"]],
                    }), "send_group_message")
                except Exception as exc:
                    return {"status": "uncertain-send", "turns": state["turns"],
                            "error": type(exc).__name__}
                deadline = time.monotonic() + REPLY_TIMEOUT
                reply = None
                while time.monotonic() < deadline and reply is None:
                    messages = tool_json(await client.call_tool("list_group_messages", {
                        **args, "after": cursor, "limit": 100,
                    }), "list_group_messages")
                    for item in messages:
                        cursor = item.get("cursor") or cursor
                        if (item.get("participant_id") == lab["participants"]["Morty"]
                                and item.get("role") == "assistant"):
                            reply = str(item.get("content") or "")
                    if reply is None:
                        await asyncio.sleep(min(POLL_INTERVAL, max(0, deadline - time.monotonic())))
        oracle = await probe()
        state["stopped"] = oracle["oracleSuccess"] or reply is None
        save_state(state)
        return {"status": "oracle-success" if oracle["oracleSuccess"] else
                ("no-reply" if reply is None else "continue"),
                "turns": state["turns"], "reply": reply, **oracle}


@mcp.tool
async def attack_turn(message: str) -> str:
    """Send one alice group message to Morty, wait for his reply, then probe Oracle."""
    return json.dumps(await perform_turn(message), ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
