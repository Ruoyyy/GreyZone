"""Offline checks for Dreadnode's code-enforced mail turns."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from mail_campaign import Campaign, Report
from mail_protocol import NoReply, target_error


class FakeTransport:
    def __init__(self, reply=True, fail=False, gate=None):
        self.reply, self.fail, self.gate = reply, fail, gate
        self.writes, self.inbox = [], []
        self.accepted = asyncio.Event()

    async def call(self, name, arguments):
        if name == "list_received":
            return {"messages": list(self.inbox)}
        if name == "read_received":
            return {"body": "Example reply", "inReplyTo": f"<sent-{arguments['index']}@local>"}
        self.writes.append(name)
        self.accepted.set()
        if self.gate:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("Connection lost after write")
        number = len(self.writes)
        if self.reply:
            self.inbox.append({"index": number, "from": "admin@gmail.com", "recipients": ["alice@gmail.com"]})
        return {"messageId": f"sent-{number}", "deliveryId": f"delivery-{number}", "rfcMessageId": f"<sent-{number}@local>"}


class CampaignTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = SimpleNamespace(managed_email="admin@gmail.com", attacker_email="alice@gmail.com",
            subject="Example", reply_timeout_seconds=0.02, poll_seconds=0.001)

    async def asyncTearDown(self):
        self.temp.cleanup()

    def campaign(self, transport, budget=2):
        @asynccontextmanager
        async def connector(config):
            yield transport
        return Campaign(self.config, budget, Report(Path(self.temp.name) / "run"), connector)

    async def test_thread_continuation_and_budget(self):
        transport = FakeTransport()
        campaign = self.campaign(transport)
        self.assertEqual((await campaign.send("first"))["turn"], 1)
        self.assertEqual((await campaign.send("second"))["turn"], 2)
        self.assertEqual((await campaign.send("third"))["status"], "stopped")
        self.assertEqual(transport.writes, ["send_email", "reply_to"])
        saved = json.loads((Path(self.temp.name) / "run/case-001-summary.json").read_text())
        self.assertEqual(saved["sent_message_ids"], ["sent-1", "sent-2"])

    async def test_concurrent_submission_is_rejected(self):
        gate = asyncio.Event()
        transport = FakeTransport(gate=gate)
        campaign = self.campaign(transport)
        pending = asyncio.create_task(campaign.send("first"))
        await transport.accepted.wait()
        self.assertEqual((await campaign.send("parallel"))["status"], "pending")
        gate.set()
        self.assertEqual((await pending)["status"], "completed")
        self.assertEqual(len(transport.writes), 1)

    async def test_uncertain_write_is_not_retried(self):
        transport = FakeTransport(fail=True)
        campaign = self.campaign(transport)
        self.assertEqual((await campaign.send("first"))["status"], "stopped")
        await campaign.send("retry")
        self.assertEqual(len(transport.writes), 1)
        self.assertEqual(campaign.status, "error")

    async def test_no_reply_is_preserved_for_phishing_oracle(self):
        transport = FakeTransport(reply=False)
        campaign = self.campaign(transport)
        self.assertEqual((await campaign.send("first"))["status"], "stopped")
        self.assertEqual(campaign.status, "no_reply")
        await campaign.send("retry")
        self.assertEqual(len(transport.writes), 1)

    async def test_mcp_group_keeps_no_reply_type(self):
        transport = FakeTransport(reply=False)

        @asynccontextmanager
        async def connector(config):
            try:
                yield transport
            except Exception as exc:
                raise ExceptionGroup("unhandled errors in a TaskGroup", [exc])

        campaign = Campaign(self.config, 2, Report(Path(self.temp.name) / "wrapped"), connector)
        await campaign.send("first")
        self.assertEqual(campaign.status, "no_reply")
        self.assertTrue(campaign.error.startswith("NoReply:"))

    async def test_mixed_group_is_not_hidden_as_no_reply(self):
        group = ExceptionGroup("mixed", [NoReply("deadline"), RuntimeError("transport cleanup failed")])
        self.assertIsNone(target_error(group))


if __name__ == "__main__":
    unittest.main()
