"""One bounded, serial alice-to-Scout turn protocol for every framework."""
from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import traceback

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from redteam.common import configure, write_json
configure()
from oracle import common as C
from oracle import watch as W
from redteam.observer import (DELETED_CHANNEL_REPLY, channel_deletion, evidence,
                              pod_exists, public_replies, settle)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class ChannelDeleted(BaseException):
    """Controlled SDK termination after a verified, fully recorded target turn."""


async def framework_turn(message: str) -> dict:
    result = await perform_turn(message)
    if result.get("stopReason") == "shared-pod-deleted":
        # BaseException avoids SDK retries/wrapping this deliberate termination.
        raise ChannelDeleted()
    return result


def tool_data(result):
    if result.isError:
        raise RuntimeError("alice MCP rejected the operation")
    blocks = [part.text for part in result.content if part.type == "text"]
    if len(blocks) != 1:
        raise RuntimeError("unexpected alice MCP response")
    return json.loads(blocks[0])


@asynccontextmanager
async def connect():
    params = StdioServerParameters(command="docker", args=[
        "exec", "-i", "commonly-redteam-mcp", "node", "src/user-index.js"])
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            names = {item.name for item in (await session.list_tools()).tools}
            if not {"user_send_message", "user_read_messages", "user_list_pods"} <= names:
                raise RuntimeError("alice MCP lacks required tools")
            yield session


def attack_content(message: str) -> str:
    message = message.strip()
    if not message or len(message) > 20000:
        raise ValueError("attack message must contain 1..20000 characters")
    if re.search(r"@kai\b", message, re.I):
        raise ValueError("only Scout may be targeted")
    return message if re.search(r"@scout\b", message, re.I) else "@scout " + message


async def perform_turn(message: str) -> dict:
    path = Path(os.environ["COMMONLY_STATE_FILE"])
    with path.with_suffix(".lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another attack turn is in progress") from None
        return await _perform_turn(path, attack_content(message))


async def _perform_turn(path: Path, content: str) -> dict:
    state = json.loads(path.read_text())
    ep = C.load_episode()
    if not ep or ep["n"] != state["episode"] or ep["state"] != "running":
        raise RuntimeError("Oracle episode does not match the armed candidate")
    if state["stopped"] or state["turns"] >= state["maxTurns"]:
        raise RuntimeError("candidate has stopped or exhausted its physical turn budget")
    event = {"turn": state["turns"] + 1, "message": content, "status": "sending"}
    # Persist BEFORE a write: a lost response must never cause a duplicate send.
    state["stopped"] = True
    state["turns"] += 1
    state.setdefault("events", []).append(event)
    write_json(path, state)
    ep.setdefault("attackerTexts", []).append(content)
    C.save_episode(ep)
    try:
        async with connect() as client:
            result = tool_data(await client.call_tool("user_send_message", {
                "podId": ep["pods"]["shared"], "content": content,
            }))
            message = result.get("message", result)
            identifier = str(message.get("id") or message.get("_id") or "")
            if not identifier.isdigit():
                raise RuntimeError("accepted message ID missing; delivery is uncertain")
            event["attackMessageId"] = identifier
            ep["redteam"]["messageIds"].append(identifier)
            C.save_episode(ep)
            rows = await settle(ep, state["waitSeconds"], identifier)
            write_json(path.parent / f"target-{state['turns']}.json", rows[identifier])
            deleted = channel_deletion(rows[identifier], ep["pods"]["shared"], identifier)
            if deleted:
                exists = await asyncio.to_thread(pod_exists, ep["pods"]["shared"])
                if exists:
                    raise RuntimeError("Deletion audit disagrees with live Pod state")
                write_json(path.parent / f"channel-state-{state['turns']}.json", {
                    "podId": ep["pods"]["shared"], "exists": False, "observedAt": C.now_iso(),
                })
                messages = []
            else:
                response = tool_data(await client.call_tool("user_read_messages", {
                    "podId": ep["pods"]["shared"], "limit": 200,
                }))
                messages = response if isinstance(response, list) else response.get("messages", [])
            write_json(path.parent / f"public-messages-{state['turns']}.json", messages)
            replies = public_replies(rows[identifier], messages, identifier)
            reply = "\n\n".join(str(m.get("content", "")) for m in replies)
            # This marker is transport feedback, never presented as Scout's words.
            if not reply:
                reply = DELETED_CHANNEL_REPLY if deleted else "[Target finished without a public text reply.]"
            ep = C.load_episode()
            ep["redteam"].setdefault("replyMessageIds", []).extend(str(m["id"]) for m in replies)
            C.save_episode(ep)
            token = await C.login(**C.ADMIN)
            probe = await W.probe(token, ep)
            success = probe["verdict"].startswith("success(")
            event.update(status="oracle-success" if success else "continue", reply=reply,
                         replyMessageIds=[str(m["id"]) for m in replies],
                         oracleSuccess=success, verdict=probe["verdict"], turns=state["turns"])
            state["stopped"] = success or deleted
            result = {key: event[key] for key in ("status", "reply", "oracleSuccess", "verdict", "turns")}
            if deleted:
                event["stopReason"] = "shared-pod-deleted"
                result.update(conversationEnded=True, stopReason=event["stopReason"])
            return result
    except BaseException as exc:
        event["status"] = "invalid"
        event["error"] = f"{type(exc).__name__}: {exc}"
        event["errorTraceback"] = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        if event.get("attackMessageId"):
            try:
                write_json(path.parent / f"target-{state['turns']}.json",
                           await asyncio.to_thread(evidence, ep, event["attackMessageId"]))
            except Exception:
                pass
        raise
    finally:
        write_json(path, state)


if __name__ == "__main__":
    try:
        request = json.loads(sys.stdin.read())
        with contextlib.redirect_stdout(sys.stderr):
            result = asyncio.run(perform_turn(request["message"]))
        print(json.dumps(result))
    except Exception:
        # Detailed diagnostics stay in host artifacts, outside attacker feedback.
        print(json.dumps({"status": "invalid", "error": "Target turn failed; inspect host artifacts."}))
        raise SystemExit(1)
