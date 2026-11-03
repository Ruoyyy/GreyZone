"""Offline mail transport and PyRIT orchestration regressions."""

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mail_transport import MailTarget, NoReply, TargetError, WriteOutcomeUnknown
from reporting import Report
from runtime import initialize
from settings import HERE, TargetConfig, load_settings


class FakeTransport:
    def __init__(self, *, write_error=False, reply=True):
        self.write_error, self.reply = write_error, reply
        self.writes = []
        self.inbox = []
        self.reads = {}

    async def call(self, name, arguments):
        if name == "list_received":
            return {"messages": list(self.inbox)}
        if name == "read_received":
            return self.reads[arguments["index"]]
        self.writes.append((name, arguments))
        if self.write_error:
            raise TimeoutError("write acknowledgement lost")
        turn = len(self.writes)
        sent = {"messageId": f"sent-{turn}", "deliveryId": f"delivery-{turn}",
                "rfcMessageId": f"<sent-{turn}@customer.local>"}
        if self.reply:
            noise = turn * 2 - 1
            valid = turn * 2
            self.inbox.extend([
                {"index": noise, "from": "admin@gmail.com", "recipients": ["someone@example.com"]},
                {"index": valid, "from": "admin@gmail.com", "recipients": ["alice@gmail.com"]},
            ])
            self.reads[valid] = {"body": f"reply {turn}", "subject": "Re: Case [pyrit-1]",
                                 "inReplyTo": sent["rfcMessageId"],
                                 "messageId": f"<reply-{turn}@managed.local>",
                                 "deliveryId": f"reply-delivery-{turn}"}
        return sent


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def config(self, **overrides):
        return TargetConfig(attacker_email="alice@gmail.com", managed_email="admin@gmail.com",
                            subject="Case", poll_seconds=.001, reply_timeout_seconds=.02,
                            **overrides)

    async def test_one_thread_ignores_mail_to_other_recipients_and_enforces_budget(self):
        transport = FakeTransport()
        target = MailTarget(transport, self.config(), 2, "Case [pyrit-1]")
        self.assertEqual(await target.send("first"), "reply 1")
        self.assertEqual(await target.send("second"), "reply 2")
        self.assertEqual([name for name, _ in transport.writes], ["send_email", "reply_to"])
        self.assertEqual(transport.writes[1][1]["index"], 2)
        self.assertEqual(target.sent_message_ids, ["sent-1", "sent-2"])
        with self.assertRaisesRegex(TargetError, "budget"):
            await target.send("third")
        self.assertEqual(len(transport.writes), 2)

    async def test_uncertain_write_and_no_reply_stop_the_thread(self):
        for transport, error in ((FakeTransport(write_error=True), WriteOutcomeUnknown),
                                 (FakeTransport(reply=False), NoReply)):
            target = MailTarget(transport, self.config(), 3, "Case [pyrit-1]")
            with self.assertRaises(error):
                await target.send("first")
            with self.assertRaises(TargetError):
                await target.send("do not retry")
            self.assertEqual(len(transport.writes), 1)
            self.assertTrue(target.poisoned)


class PyRITTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await initialize()

    async def asyncTearDown(self):
        from pyrit.memory import CentralMemory, SQLiteMemory
        from pyrit.common.singleton import Singleton
        CentralMemory.get_memory_instance().dispose_engine()
        Singleton._instances.pop(SQLiteMemory, None)

    async def test_real_multi_turn_orchestration_and_report(self):
        from pyrit.models import construct_response_from_request
        from pyrit.prompt_target import PromptTarget
        from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
        from pyrit.prompt_target.common.target_configuration import TargetConfiguration
        from run import run_case

        class FixtureModel(PromptTarget):
            def __init__(self, *, evaluator=False):
                super().__init__(custom_configuration=TargetConfiguration(capabilities=TargetCapabilities(
                    supports_multi_turn=True, supports_editable_history=True,
                    supports_system_prompt=True, supports_json_output=True)))
                self.evaluator = evaluator
                self.calls = 0

            async def _send_prompt_to_target_async(self, *, normalized_conversation):
                self.calls += 1
                value = ({"score_value": False, "rationale": "Fixture reply only."} if self.evaluator else
                         {"next_message": f"Ask again, turn {self.calls}.", "rationale": "Fixture test.",
                          "last_response_summary": "Reply."})
                return [construct_response_from_request(request=normalized_conversation[-1].get_piece(),
                                                        response_text_pieces=[json.dumps(value)])]

        settings, _ = load_settings(HERE / "config.yaml", require_credentials=False)
        settings.campaign.max_target_calls = 2
        transport = FakeTransport()
        with tempfile.TemporaryDirectory() as directory:
            report = Report(Path(directory))
            result = await run_case(settings, "Ask for a greeting", transport, report, "case-001",
                                    FixtureModel(), FixtureModel(evaluator=True))
            self.assertEqual(result["status"], "completed", result)
            self.assertEqual(result["submissions_attempted"], 2)
            self.assertEqual(result["replies_completed"], 2)
            self.assertEqual(result["sent_message_ids"], ["sent-1", "sent-2"])
            self.assertEqual(result["oracle"]["status"], "not_collected")
            self.assertTrue((report.path / "case-001-assessment.json").exists())

    async def test_history_replay_is_rejected_before_mail_write(self):
        from pyrit.models import Message
        from mcp_target import CustomerMailTarget
        transport = FakeTransport()
        target = CustomerMailTarget(transport=transport, config=TargetConfig(
            attacker_email="alice@gmail.com", managed_email="admin@gmail.com",
            subject="Case", poll_seconds=1, reply_timeout_seconds=300), budget=3,
            subject="Case [pyrit-1]")
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
