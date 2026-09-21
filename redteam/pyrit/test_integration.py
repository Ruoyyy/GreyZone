"""Offline regressions using real PyRIT orchestration and fake external services."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from issue_transport import IssueTarget, TargetError, WriteOutcomeUnknown
from reporting import Report
from settings import HERE, TargetConfig, load_settings
from runtime import initialize


class FakeTransport:
    def __init__(self, *, write_error=False, reply="finished", empty=False):
        self.writes = []
        self.write_error, self.reply, self.empty = write_error, reply, empty

    async def call(self, name, arguments):
        if name == "issue_write":
            self.writes.append(arguments)
            if self.write_error:
                raise TimeoutError("write acknowledgement lost")
            return {"number": 7, "id": 10 * len(self.writes)}
        if self.empty:
            return []
        return [{"id": 10 * len(self.writes) + 1, "user": {"login": "gitea-actions"},
                 "body": f"**Letta Code {self.reply}**\nHello from the fixture."}]


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_persistent_issue_and_budget(self):
        transport = FakeTransport()
        target = IssueTarget(transport, TargetConfig(), 2)
        await target.send("first")
        await target.send("second")
        self.assertEqual([w["method"] for w in transport.writes], ["create", "add_comment"])
        self.assertEqual(transport.writes[1]["issue_number"], 7)
        with self.assertRaisesRegex(TargetError, "budget"):
            await target.send("third")
        self.assertEqual(len(transport.writes), 2)

    async def test_uncertain_write_is_not_retried(self):
        transport = FakeTransport(write_error=True)
        target = IssueTarget(transport, TargetConfig(), 3)
        with self.assertRaises(WriteOutcomeUnknown):
            await target.send("hello")
        with self.assertRaises(TargetError):
            await target.send("retry")
        self.assertEqual(len(transport.writes), 1)

    async def test_timeout_and_failed_reply_stop_case(self):
        for transport in (FakeTransport(empty=True), FakeTransport(reply="failed")):
            target = IssueTarget(transport, TargetConfig(poll_seconds=.001, reply_timeout_seconds=.01), 3)
            with self.assertRaises(TargetError):
                await target.send("hello")
            self.assertTrue(target.poisoned)
            self.assertEqual(target.completed, 0)
            with self.assertRaises(TargetError):
                await target.send("retry")
            self.assertEqual(len(transport.writes), 1)


class PyRITTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await initialize()

    async def asyncTearDown(self):
        from pyrit.memory import CentralMemory
        CentralMemory.get_memory_instance().dispose_engine()
        from pyrit.common.singleton import Singleton
        from pyrit.memory import SQLiteMemory
        Singleton._instances.pop(SQLiteMemory, None)

    async def test_vendor_parameters_through_real_sdk(self):
        import httpx
        from openai import AsyncOpenAI
        from pyrit.models import Message
        from models import build_model
        from runtime import close_models

        settings, _ = load_settings(HERE / "config.yaml", require_credentials=False)
        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={
                "id": "fixture", "object": "chat.completion", "created": 0,
                "model": "glm-4.7", "choices": [{"index": 0, "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "hello"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

        for config in (settings.models.attacker, settings.models.evaluator):
            model = build_model(config)
            await close_models(model)
            model._async_client = AsyncOpenAI(api_key="fixture-key", base_url="https://fixture.invalid/v1",
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
            try:
                response = await model.send_prompt_async(message=Message.from_prompt(prompt="Hello", role="user"))
                self.assertEqual(response[0].get_value(), "hello")
                self.assertEqual(requests[-1]["thinking"], {"type": "disabled"})
                self.assertEqual(requests[-1]["max_tokens"], config.max_tokens)
                self.assertNotIn("extra_body", requests[-1])
            finally:
                await close_models(model)
        self.assertEqual(len(requests), 2)

    async def test_real_multi_turn_orchestration_and_report(self):
        from pyrit.models import construct_response_from_request
        from pyrit.prompt_target import PromptTarget
        from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
        from pyrit.prompt_target.common.target_configuration import TargetConfiguration
        from run import run_case

        class FixtureModel(PromptTarget):
            def __init__(self, *, evaluator=False):
                super().__init__(custom_configuration=TargetConfiguration(capabilities=TargetCapabilities(
                    supports_multi_turn=True, supports_editable_history=True, supports_system_prompt=True, supports_json_output=True)))
                self.evaluator = evaluator
                self.calls = 0

            async def _send_prompt_to_target_async(self, *, normalized_conversation):
                self.calls += 1
                value = ({"score_value": False, "rationale": "Fixture greeting only."} if self.evaluator else
                         {"next_message": f"Say hello, turn {self.calls}.", "rationale": "Fixture test.",
                          "last_response_summary": "Greeting."})
                return [construct_response_from_request(request=normalized_conversation[-1].get_piece(),
                                                        response_text_pieces=[json.dumps(value)])]

        settings, _ = load_settings(HERE / "config.yaml", require_credentials=False)
        settings.campaign.max_target_calls = 2
        transport = FakeTransport()
        with tempfile.TemporaryDirectory() as directory:
            report = Report(Path(directory))
            result = await run_case(settings, "Say hello", transport, report, "case-001",
                                    FixtureModel(), FixtureModel(evaluator=True))
            self.assertEqual(result["status"], "completed", result)
            self.assertEqual(result["submissions_attempted"], 2)
            self.assertEqual(result["replies_completed"], 2)
            self.assertEqual(result["oracle"]["status"], "not_collected")
            self.assertTrue((report.path / "case-001-assessment.json").exists())

    async def test_history_rewrite_rejected_before_write(self):
        from pyrit.models import Message
        from mcp_target import GiteaIssueTarget
        transport = FakeTransport()
        target = GiteaIssueTarget(transport=transport, config=TargetConfig(), budget=3)
        first = Message.from_prompt(prompt="hello", role="user")
        await target._send_prompt_to_target_async(normalized_conversation=[first])
        with self.assertRaises(TargetError):
            await target._send_prompt_to_target_async(normalized_conversation=[first])
        self.assertEqual(len(transport.writes), 1)


class ReportTests(unittest.TestCase):
    def test_secret_redaction_and_private_files(self):
        secret = 'fixture-token-"with\\escapes'
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"TEST_API_KEY": secret}):
            report = Report(Path(directory))
            report.write("summary.json", {"value": secret})
            target = report.path / "summary.json"
            self.assertEqual(json.loads(target.read_text())["value"], "[REDACTED]")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
