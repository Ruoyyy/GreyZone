"""Offline protocol tests: no real Gitea writes or model requests."""
import asyncio
import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

from letta_campaign import Campaign, Report


class FakeTransport:
    def __init__(self, mode='normal'):
        self.mode, self.writes, self.reads = mode, [], 0
        self.started, self.release = asyncio.Event(), asyncio.Event()

    async def call(self, name, arguments):
        if name == 'issue_write':
            self.writes.append(arguments)
            if self.mode == 'unknown':
                raise TimeoutError('Acknowledgement lost')
            return {'number': 7, 'id': 20}
        self.reads += 1
        self.started.set()
        if self.mode == 'pending':
            await self.release.wait()
        if self.mode == 'timeout':
            return []
        if self.mode == 'failed':
            return [{'id': 11, 'user': {'login': 'gitea-actions'}, 'body': '**Letta Code failed**'}]
        old = {'id': 11, 'user': {'login': 'gitea-actions'}, 'body': '**Letta Code finished**\nFirst reply'}
        if len(self.writes) == 1:
            return [old]
        if self.reads == 2:
            return [old, {'id': 21, 'user': {'login': 'gitea-actions'}, 'body': '**Letta Code working**'}]
        return [old, {'id': 21, 'user': {'login': 'gitea-actions'}, 'body': '**Letta Code finished**\nSecond reply'}]


class CampaignTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'run'
        self.config = SimpleNamespace(mcp_url='http://fixture.invalid/mcp', web_url='http://fixture.invalid',
            owner='owner', repo='repo', title='Fixture', poll_seconds=0,
            reply_timeout_seconds=0.05, request_timeout_seconds=1)

    def campaign(self, mode='normal'):
        transport = FakeTransport(mode)
        @asynccontextmanager
        async def connector(config):
            yield transport
        return Campaign(self.config, 2, Report(self.directory), connector), transport

    async def test_two_turns_ignore_old_and_progress_replies_and_enforce_budget(self):
        campaign, transport = self.campaign()
        first = await campaign.send('First attack')
        second = await campaign.send('Second attack')
        self.assertEqual((first['reply_comment_id'], second['reply_comment_id']), (11, 21))
        self.assertEqual(second['reply'], 'Second reply')
        self.assertEqual(second['remaining_turns'], 0)
        self.assertEqual((await campaign.send('Over budget'))['reason'], 'turn_budget_exhausted')
        self.assertEqual([w['method'] for w in transport.writes], ['create', 'add_comment'])
        self.assertEqual(transport.writes[1]['issue_number'], 7)
        self.assertEqual(transport.reads, 3)
        summary = json.loads((self.directory / 'case-001-summary.json').read_text())
        self.assertEqual(summary['status'], 'completed')
        self.assertEqual(summary['submissions_attempted'], summary['replies_completed'])
        events = [json.loads(line)['event'] for line in (self.directory / 'events.jsonl').read_text().splitlines()]
        self.assertEqual(events, ['submit_intent', 'submitted', 'reply', 'submit_intent', 'submitted', 'reply'])

    async def test_concurrent_submission_is_rejected_without_queuing(self):
        campaign, transport = self.campaign('pending')
        first = asyncio.create_task(campaign.send('First'))
        await transport.started.wait()
        second = await campaign.send('Concurrent')
        self.assertEqual(second['status'], 'pending')
        self.assertEqual(len(transport.writes), 1)
        transport.release.set()
        self.assertEqual((await first)['status'], 'completed')
        await asyncio.sleep(0)
        self.assertEqual(len(transport.writes), 1)

    async def test_uncertain_write_stops_without_retry(self):
        campaign, transport = self.campaign('unknown')
        response = await campaign.send('Attack')
        self.assertEqual(response['status'], 'stopped')
        self.assertIn('WriteOutcomeUnknown', response['error'])
        self.assertEqual((await campaign.send('Retry'))['status'], 'stopped')
        self.assertEqual(len(transport.writes), 1)

    async def test_timeout_and_failed_completion_stop_later_writes(self):
        for mode in ('timeout', 'failed'):
            with self.subTest(mode=mode):
                self.directory = Path(self.temp.name) / mode
                campaign, transport = self.campaign(mode)
                self.assertEqual((await campaign.send('Attack'))['status'], 'stopped')
                self.assertEqual((await campaign.send('Retry'))['status'], 'stopped')
                self.assertEqual(len(transport.writes), 1)
                summary = json.loads((self.directory / 'case-001-summary.json').read_text())
                self.assertEqual(summary['status'], 'error')
                self.assertTrue(summary['pending_or_uncertain'])

    async def test_cancelled_turn_cannot_be_replayed(self):
        campaign, transport = self.campaign('pending')
        pending = asyncio.create_task(campaign.send('Attack'))
        await transport.started.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertEqual((await campaign.send('Retry'))['status'], 'stopped')
        self.assertEqual(len(transport.writes), 1)

    async def test_duplicate_runtime_cannot_reuse_report_directory(self):
        self.campaign()
        with self.assertRaises(FileExistsError):
            self.campaign()


if __name__ == '__main__':
    unittest.main()
