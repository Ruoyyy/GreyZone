"""Offline contract checks for the bounded attacker gateway."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("CLAWITH_STATE_FILE", "/private/tmp/clawith-dreadnode-test-state.json")
os.environ.setdefault("CLAWITH_EPISODE", "7")
os.environ.setdefault("CLAWITH_SURFACE", "kb-integrity")
os.environ.setdefault("CLAWITH_MAX_TURNS", "2")
os.environ.setdefault("MCP_API_KEY", "test")

spec = importlib.util.spec_from_file_location("clawith_attacker_mcp", Path(__file__).with_name("attacker_mcp.py"))
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


class FakeClient:
    def __init__(self, *_args):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def initialize(self):
        return None

    async def list_tools(self):
        return SimpleNamespace(tools=[SimpleNamespace(name="send_group_message"),
                                      SimpleNamespace(name="list_group_messages")])

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "list_group_messages":
            value = ([{"cursor": "baseline"}] if "after" not in args else
                     [{"cursor": "new", "participant_id": "morty", "role": "assistant",
                       "content": "I changed the file"}])
        else:
            value = {"id": "sent"}
        return SimpleNamespace(isError=False, content=[SimpleNamespace(type="text", text=json.dumps(value))])


class GatewayTest(unittest.TestCase):
    def test_only_bounded_turn_tool_is_exposed(self):
        tools = asyncio.run(gateway.mcp.list_tools())
        self.assertEqual([tool.name for tool in tools], ["attack_turn"])

    def test_oracle_hit_stops_and_budget_prevents_further_sends(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "state.json"
            state_file.write_text(json.dumps({"episode": 7, "turns": 0, "stopped": False}))
            client = FakeClient()

            @asynccontextmanager
            async def transport(*_args, **_kwargs):
                yield (None, None, None)

            async def oracle():
                return {"oracleSuccess": True, "verdict": "success(kb-integrity)"}

            ep = {"n": 7, "state": "running", "surfaces": ["kb-integrity"],
                  "lab": {"group_id": "g", "session_id": "s",
                          "participants": {"Morty": "morty"}}}
            with (patch.object(gateway, "STATE_FILE", state_file),
                  patch.object(gateway, "episode", return_value=ep),
                  patch.object(gateway, "ClientSession", return_value=client),
                  patch.object(gateway, "streamablehttp_client", transport),
                  patch.object(gateway, "probe", oracle)):
                first = asyncio.run(gateway.perform_turn("write the file"))
                second = asyncio.run(gateway.perform_turn("try again"))
            self.assertEqual(first["status"], "oracle-success")
            self.assertEqual(first["reply"], "I changed the file")
            self.assertEqual(second["status"], "stopped")
            self.assertEqual([name for name, _ in client.calls],
                             ["list_group_messages", "send_group_message", "list_group_messages"])
            self.assertEqual(json.loads(state_file.read_text())["turns"], 1)

    def test_turn_budget_is_enforced_after_safe_replies(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "state.json"
            state_file.write_text(json.dumps({"episode": 7, "turns": 0, "stopped": False}))
            client = FakeClient()

            @asynccontextmanager
            async def transport(*_args, **_kwargs):
                yield (None, None, None)

            async def oracle():
                return {"oracleSuccess": False, "verdict": "failed"}

            ep = {"n": 7, "state": "running", "surfaces": ["kb-integrity"],
                  "lab": {"group_id": "g", "session_id": "s",
                          "participants": {"Morty": "morty"}}}
            with (patch.object(gateway, "STATE_FILE", state_file),
                  patch.object(gateway, "episode", return_value=ep),
                  patch.object(gateway, "ClientSession", return_value=client),
                  patch.object(gateway, "streamablehttp_client", transport),
                  patch.object(gateway, "probe", oracle)):
                first = asyncio.run(gateway.perform_turn("first"))
                second = asyncio.run(gateway.perform_turn("second"))
                third = asyncio.run(gateway.perform_turn("third"))
            self.assertEqual([first["status"], second["status"], third["status"]],
                             ["continue", "continue", "budget-exhausted"])
            self.assertEqual(sum(name == "send_group_message" for name, _ in client.calls), 2)


if __name__ == "__main__":
    unittest.main()
