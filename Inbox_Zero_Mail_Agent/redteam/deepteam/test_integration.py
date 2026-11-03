"""Offline checks for DeepTeam mail turns and partial-result handling."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from reporting import Report
from run import run_case
from settings import HERE, load_settings


class FakeTransport:
    def __init__(self, *, reply=True):
        self.reply = reply
        self.writes = []
        self.inbox = []
        self.reads = {}

    async def call(self, name, arguments):
        if name == "list_received":
            return {"messages": list(self.inbox)}
        if name == "read_received":
            return self.reads[arguments["index"]]
        self.writes.append((name, arguments))
        number = len(self.writes)
        sent = {"messageId": f"sent-{number}", "deliveryId": f"delivery-{number}",
                "rfcMessageId": f"<sent-{number}@customer.local>"}
        if self.reply:
            index = number
            self.inbox.append({"index": index, "from": "admin@gmail.com",
                               "recipients": ["alice@gmail.com"]})
            self.reads[index] = {"body": f"reply {number}",
                                 "subject": "Re: Project access question",
                                 "inReplyTo": sent["rfcMessageId"]}
        return sent


class FakeAssessment:
    def __init__(self):
        self.test_cases = [SimpleNamespace(score=0, reason="No disclosure", error=None)]

    def model_dump(self, mode):
        return {"test_cases": [{"score": 0, "reason": "No disclosure", "error": None}]}


class TwoTurnTeamer:
    async def a_red_team(self, *, model_callback, **kwargs):
        from deepteam.test_case import RTTurn

        first = await model_callback("first question", [])
        await model_callback("follow up", [RTTurn(role="user", content="first question"), first])
        return FakeAssessment()


class OneTurnTeamer:
    async def a_red_team(self, *, model_callback, **kwargs):
        await model_callback("first question", [])
        return FakeAssessment()


class RewoundTeamer:
    async def a_red_team(self, *, model_callback, **kwargs):
        await model_callback("first question", [])
        await model_callback("replayed question", [])
        return FakeAssessment()


class MailDeepTeamTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_turns_use_one_thread_and_report_sent_ids(self):
        settings, _ = load_settings(HERE / "config.yaml", require_credentials=False)
        settings.campaign.max_target_calls = 2
        transport = FakeTransport()
        with tempfile.TemporaryDirectory() as directory, patch(
            "run.build_teamer", return_value=(TwoTurnTeamer(), object(), object())
        ):
            report = Report(Path(directory))
            result = await run_case(settings, "objective", transport, report,
                                    "case-001", object(), object())
            self.assertEqual(result["status"], "completed", result)
            self.assertEqual(result["sent_message_ids"], ["sent-1", "sent-2"])
            self.assertEqual(result["replies_completed"], 2)
            self.assertEqual([name for name, _ in transport.writes], ["send_email", "reply_to"])
            self.assertEqual(transport.writes[1][1]["index"], 1)
            self.assertIn("[deepteam-", transport.writes[0][1]["subject"])
            self.assertTrue((report.path / "case-001-assessment.json").exists())

    async def test_no_customer_reply_stops_case_without_success_claim(self):
        settings, _ = load_settings(HERE / "config.yaml", require_credentials=False)
        settings.target.poll_seconds = .001
        settings.target.reply_timeout_seconds = .01
        transport = FakeTransport(reply=False)
        with tempfile.TemporaryDirectory() as directory, patch(
            "run.build_teamer", return_value=(OneTurnTeamer(), object(), object())
        ):
            report = Report(Path(directory))
            result = await run_case(settings, "objective", transport, report,
                                    "case-001", object(), object())
            self.assertEqual(result["status"], "no_reply", result)
            self.assertEqual(result["sent_message_ids"], ["sent-1"])
            self.assertEqual(result["oracle"]["status"], "not_collected")
            self.assertTrue(result["pending_or_uncertain"])

    async def test_rewound_history_does_not_send_another_email(self):
        settings, _ = load_settings(HERE / "config.yaml", require_credentials=False)
        transport = FakeTransport()
        with tempfile.TemporaryDirectory() as directory, patch(
            "run.build_teamer", return_value=(RewoundTeamer(), object(), object())
        ):
            report = Report(Path(directory))
            result = await run_case(settings, "objective", transport, report,
                                    "case-001", object(), object())
            self.assertEqual(result["status"], "execution_error", result)
            self.assertEqual(len(transport.writes), 1)


if __name__ == "__main__":
    unittest.main()
