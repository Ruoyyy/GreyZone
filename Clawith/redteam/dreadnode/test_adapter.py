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
from unittest.mock import AsyncMock, patch

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
                     [{"id": "reply", "cursor": "new", "participant_id": "morty", "role": "assistant",
                       "content": "I changed the file"}])
        else:
            value = {"message": {"id": "sent"}, "run_ids": ["00000000-0000-0000-0000-000000000001"]}
        return SimpleNamespace(isError=False, content=[SimpleNamespace(type="text", text=json.dumps(value))])


class GatewayTest(unittest.TestCase):
    def test_state_query_failure_is_not_wrapped_or_resent(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "state.json"
            state_file.write_text(json.dumps({"episode": 7, "turns": 0, "stopped": False}))
            client = FakeClient()

            @asynccontextmanager
            async def transport(*_args, **_kwargs):
                yield (None, None, None)

            ep = {"n": 7, "state": "running", "surfaces": ["kb-integrity"],
                  "lab": {"group_id": "g", "session_id": "s", "participants": {"Morty": "morty"}}}
            error = gateway.RunStateQueryError({"http_status": 409, "detail": "inconsistent_checkpoint",
                                               "attempt": 6, "retry": False})
            with (patch.object(gateway, "STATE_FILE", state_file),
                  patch.object(gateway, "episode", return_value=ep),
                  patch.object(gateway, "ClientSession", return_value=client),
                  patch.object(gateway, "streamablehttp_client", transport),
                  patch.object(gateway, "read_states", side_effect=error),
                  patch.object(gateway, "probe", AsyncMock(return_value={"oracleSuccess": False, "verdict": "failed"}))):
                first = asyncio.run(gateway.perform_turn("try"))
                second = asyncio.run(gateway.perform_turn("try again"))
            self.assertEqual(first["status"], "target-error")
            self.assertEqual(first["target_error"]["http_status"], 409)
            self.assertEqual(first["target_error"]["detail"], "inconsistent_checkpoint")
            self.assertEqual(second["status"], "stopped")
            self.assertEqual(sum(name == "send_group_message" for name, _ in client.calls), 1)

    def test_failed_target_stops_even_when_oracle_fires(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "state.json"
            state_file.write_text(json.dumps({"episode": 7, "turns": 0, "stopped": False}))
            client = FakeClient()

            @asynccontextmanager
            async def transport(*_args, **_kwargs):
                yield (None, None, None)

            ep = {"n": 7, "state": "running", "surfaces": ["kb-integrity"],
                  "lab": {"group_id": "g", "session_id": "s", "participants": {"Morty": "morty"}}}
            with (patch.object(gateway, "STATE_FILE", state_file),
                  patch.object(gateway, "episode", return_value=ep),
                  patch.object(gateway, "ClientSession", return_value=client),
                  patch.object(gateway, "streamablehttp_client", transport),
                  patch.object(gateway, "read_states", return_value=[{"run_id": "00000000-0000-0000-0000-000000000001", "status": "failed", "source_message_id": "sent", "reply_message_ids": ["reply"], "delivery_status": "delivered"}]),
                  patch.object(gateway, "probe", AsyncMock(return_value={"oracleSuccess": True, "verdict": "success"}))):
                first = asyncio.run(gateway.perform_turn("try"))
                second = asyncio.run(gateway.perform_turn("try again"))
            self.assertEqual(first["status"], "target-error")
            self.assertEqual(first["target_error"]["code"], "target_failed")
            self.assertEqual(second["status"], "stopped")
            self.assertEqual(sum(name == "send_group_message" for name, _ in client.calls), 1)

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
                  patch.object(gateway, "read_states", return_value=[{"run_id": "00000000-0000-0000-0000-000000000001", "status": "completed", "source_message_id": "sent", "reply_message_ids": ["reply"], "delivery_status": "delivered"}]),
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
                  patch.object(gateway, "read_states", return_value=[{"run_id": "00000000-0000-0000-0000-000000000001", "status": "completed", "source_message_id": "sent", "reply_message_ids": ["reply"], "delivery_status": "delivered"}]),
                  patch.object(gateway, "probe", oracle)):
                first = asyncio.run(gateway.perform_turn("first"))
                second = asyncio.run(gateway.perform_turn("second"))
                third = asyncio.run(gateway.perform_turn("third"))
            self.assertEqual([first["status"], second["status"], third["status"]],
                             ["continue", "continue", "budget-exhausted"])
            self.assertEqual(sum(name == "send_group_message" for name, _ in client.calls), 2)


if __name__ == "__main__":
    unittest.main()
