"""No paid calls or issue writes: real SDK with mocked HTTP/MCP responses."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from lakera_red_sdk import LakeraRedClient, ScanResults, SessionMessage
from pydantic import ValidationError

from campaign import Campaign, SessionFailure, collect_results, validate_results
from reporting import Report, exception_details, exception_summary
from run import ReportLogger
from sdk_adapter import bind_scan, create_scan
from settings import HERE, TargetConfig, load_settings


class FakeMCP:
    def __init__(self, *, error=False, failed=False, empty=False):
        self.writes = []
        self.error, self.failed, self.empty = error, failed, empty

    async def call(self, name, arguments):
        if name == "issue_write":
            self.writes.append(arguments)
            if self.error:
                raise TimeoutError("Acknowledgement lost")
            return {"number": 7, "id": 10 * len(self.writes)}
        if self.empty:
            return []
        return [{"id": 10 * len(self.writes) + 1, "user": {"login": "gitea-actions"},
                 "body": f"**Letta Code {'failed' if self.failed else 'finished'}**\nFixture reply."}]


class FakeSession:
    def __init__(self, session_id="session-1", messages=("Hello", "Continue"), response_error=False):
        self.id, self.messages = session_id, messages
        self.responses = []
        self.response_error = response_error

    async def respond(self, reply):
        self.responses.append(reply)
        if self.response_error:
            raise TimeoutError("Red response acknowledgement lost")

    async def __aiter__(self):
        for text in self.messages:
            yield SessionMessage(attack=text, respond=self.respond)


class Cases(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.report = Report(Path(self.directory.name))
        self.settings, self.objective = load_settings(HERE / "config.yaml", False)

    async def test_multi_turn_session_and_duplicate_session(self):
        mcp = FakeMCP()
        campaign = Campaign(self.settings, mcp, self.report)
        session = FakeSession()
        await campaign.handle(session)
        self.assertTrue(campaign.complete)
        self.assertEqual([x["method"] for x in mcp.writes], ["create", "add_comment"])
        self.assertEqual(mcp.writes[1]["issue_number"], 7)
        self.assertEqual(session.responses, ["Fixture reply."] * 2)
        with self.assertRaises(SessionFailure):
            await campaign.handle(session)
        self.assertEqual(len(mcp.writes), 2)

    async def test_turn_budget_and_replayed_text(self):
        for texts in (("one", "two", "three"), ("one", "one")):
            mcp = FakeMCP()
            self.settings.campaign.max_target_calls = 2
            campaign = Campaign(self.settings, mcp, self.report)
            with self.assertRaises(SessionFailure):
                await campaign.handle(FakeSession(messages=texts))
            self.assertFalse(campaign.complete)
            self.assertLessEqual(len(mcp.writes), 2)

    async def test_write_failure_and_response_failure_stop_followups(self):
        for mcp, response_error, status in (
            (FakeMCP(error=True), False, "write_outcome_unknown"),
            (FakeMCP(failed=True), False, "execution_error"),
            (FakeMCP(), True, "response_outcome_unknown"),
        ):
            campaign = Campaign(self.settings, mcp, self.report)
            with self.assertRaises(SessionFailure):
                await campaign.handle(FakeSession(response_error=response_error))
            self.assertEqual(campaign.sessions["session-1"]["status"], status)
            with self.assertRaises(SessionFailure):
                await campaign.handle(FakeSession("session-2"))
            self.assertEqual(len(mcp.writes), 1)

    async def test_timeout_during_blocked_read_persists_partial_case(self):
        class BlockedMCP(FakeMCP):
            async def call(self, name, arguments):
                if name == "issue_read":
                    await asyncio.sleep(10)
                return await super().call(name, arguments)
        self.settings.target.reply_timeout_seconds = .02
        mcp = BlockedMCP()
        campaign = Campaign(self.settings, mcp, self.report)
        with self.assertRaises(SessionFailure):
            await campaign.handle(FakeSession())
        summary = json.loads((self.report.path / "case-001-summary.json").read_text())
        self.assertEqual(summary["status"], "target_timeout")
        self.assertTrue(summary["pending_or_uncertain"])
        self.assertEqual(summary["issue_number"], 7)
        self.assertEqual(len(mcp.writes), 1)

    async def test_cancelled_wait_preserves_pending_issue(self):
        started = asyncio.Event()
        class BlockedMCP(FakeMCP):
            async def call(self, name, arguments):
                if name == "issue_read":
                    started.set()
                    await asyncio.Event().wait()
                return await super().call(name, arguments)
        campaign = Campaign(self.settings, BlockedMCP(), self.report)
        task = asyncio.create_task(campaign.handle(FakeSession()))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(campaign.sessions["session-1"]["status"], "interrupted")
        self.assertTrue(campaign.sessions["session-1"]["pending_or_uncertain"])

    async def test_real_sdk_wire_contract_and_results(self):
        requests = []
        sent = 0
        replies = []
        ended = False
        campaign = Campaign(self.settings, FakeMCP(), self.report)
        async def service(request):
            nonlocal sent, ended
            await asyncio.sleep(0)
            path = request.url.path
            body = json.loads(request.content) if request.content else None
            requests.append((path, body))
            if path == "/relay/targets":
                return httpx.Response(200, json={"targetId": "target-1", "name": "fixture", "recon": body["appContext"]})
            if path == "/relay/scans":
                return httpx.Response(200, json={"scanId": "scan-1", "dashboardLink": "https://red.lakera.ai/scans/scan-1"})
            if path.endswith("/start"):
                return httpx.Response(200, json={})
            if path.endswith("/response"):
                replies.append(body["message"])
                return httpx.Response(200, json={})
            if path == "/relay/messages":
                if sent < 2 and len(replies) == sent:
                    sent += 1
                    return httpx.Response(200, json={"sessionId": "session-1", "payload": f"Greeting {sent}"})
                if len(replies) == 2:
                    if not ended:
                        ended = True
                        return httpx.Response(200, json={"type": "end_session", "sessionId": "session-1"})
                    # Let the SDK's session iterator consume its terminal message.
                    while campaign.active_session is not None:
                        await asyncio.sleep(0)
                    return httpx.Response(200, json={"type": "end_scope"})
                # Yield until a handler replies instead of introducing test backoff.
                await asyncio.sleep(.001)
                return await service(request)
            if path.endswith("/results"):
                return httpx.Response(200, json={"ready": True, "results": [{
                    "objectiveId": self.settings.campaign.objective_key, "isSuccessful": False,
                    "evaluation": {"attackSuccessScore": 0, "explanation": "Fixture."}}]})
            self.fail(f"Unexpected SDK request: {path}")

        logger = ReportLogger(self.report)
        async with LakeraRedClient(api_key="fixture-key", base_url="https://fixture.invalid", logger=logger) as client:
            await client._http_client.aclose()
            async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as http:
                client._http_client = http  # Test seam only; production uses public API.
                self.settings.red.base_url = "https://fixture.invalid"
                scan = await create_scan(client, http, self.settings, self.objective,
                                         self.report, "fixture-key", logger)
                async with asyncio.timeout(5):
                    await scan.run(campaign.handle)
                results = await collect_results(scan, self.report, 1)
        self.assertTrue(campaign.complete)
        self.assertTrue(validate_results(results, self.settings))
        wire = next(body for path, body in requests if path == "/relay/scans")
        self.assertEqual(wire["maxConcurrentRequests"], 1)
        self.assertEqual(wire["strategyParams"]["maxRetries"], 0)
        self.assertEqual(wire["strategyParams"]["numberOfAttacksPerObjective"], 1)
        self.assertEqual(wire["customObjectives"][0]["key"], self.settings.campaign.objective_key)
        self.assertNotIn("objectiveIds", wire)
        self.assertFalse(any("recon" in path for path, _ in requests))

    async def test_real_sdk_waits_past_180_and_target_stops_at_300(self):
        import lakera_red_sdk._message_loop as loop
        for reply_at, expected in ((299, "completed"), (301, "target_timeout")):
            clock = [0.0]
            release = asyncio.Event()
            terminal = asyncio.Event()
            sent = False
            ended = False
            class DelayedMCP(FakeMCP):
                async def call(self, name, arguments):
                    if name == "issue_read":
                        await release.wait()
                    return await super().call(name, arguments)
            mcp = DelayedMCP()
            campaign = Campaign(self.settings, mcp, self.report)
            async def fast_sleep(seconds):
                clock[0] = min(reply_at, clock[0] + seconds)
                if clock[0] >= reply_at:
                    release.set()
                await asyncio.sleep(0)
            async def service(request):
                nonlocal sent, ended
                await asyncio.sleep(0)
                path = request.url.path
                if path.endswith("/start"):
                    return httpx.Response(200, json={})
                if path.endswith("/response") or path.endswith("/error"):
                    terminal.set()
                    return httpx.Response(200, json={})
                if not sent:
                    sent = True
                    return httpx.Response(200, json={"sessionId": "session-1", "payload": "Hello"})
                if terminal.is_set():
                    if not ended:
                        ended = True
                        return httpx.Response(200, json={"type": "end_session", "sessionId": "session-1"})
                    while campaign.active_session is not None:
                        await asyncio.sleep(0)
                    return httpx.Response(200, json={"type": "end_scope"})
                return httpx.Response(204)
            logger = ReportLogger(self.report)
            async with httpx.AsyncClient(transport=httpx.MockTransport(service)) as http:
                scan = bind_scan(SimpleNamespace(scan_id="scan-1", dashboard_link="fixture"),
                                 "target-1", http, self.settings, "fixture", logger)
                # Virtual time advances the SDK's genuine idle/backoff loop, not
                # the application callback; asyncio task deadlines stay real.
                async_proxy = SimpleNamespace(**{k: getattr(asyncio, k) for k in dir(asyncio)})
                async_proxy.sleep = fast_sleep
                with patch.object(loop, "time", SimpleNamespace(monotonic=lambda: clock[0])), \
                     patch.object(loop, "asyncio", async_proxy), \
                     patch("issue_transport.monotonic", side_effect=lambda: clock[0]):
                    async with asyncio.timeout(5):
                        await scan.run(campaign.handle)
            self.assertEqual(campaign.sessions["session-1"]["status"], expected)
            self.assertFalse(logger.idle_timeout)
            self.assertEqual(len(mcp.writes), 1)

    async def test_result_polling_waits_for_ready(self):
        class Results:
            count = 0
            async def get_results(self):
                self.count += 1
                return ScanResults(ready=self.count == 2, results=[])
        scan = Results()
        result = await collect_results(scan, self.report, 1, poll_seconds=.001)
        self.assertEqual(scan.count, 2)
        self.assertFalse(validate_results(result, self.settings))

    def test_deadline_validation_and_redaction(self):
        with self.assertRaises(ValidationError):
            TargetConfig(reply_timeout_seconds=301)
        self.assertEqual(TargetConfig().reply_timeout_seconds, 300)
        secret = 'fixture-secret-"\\value'
        with patch.dict(os.environ, {"LAKERA_RED_API_KEY": secret}):
            report = Report(Path(self.directory.name))
            report.write("secret.json", {"value": secret})
            path = report.path / "secret.json"
            self.assertEqual(json.loads(path.read_text())["value"], "[REDACTED]")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_nested_sdk_failure_preserves_root_cause(self):
        error = ExceptionGroup("TaskGroup", [ExceptionGroup("nested", [
            RuntimeError("getTarget failed: HTTP 401: Unauthorized")])])
        self.assertEqual(exception_summary(error),
                         "RuntimeError: getTarget failed: HTTP 401: Unauthorized")
        details = exception_details(error)
        self.assertEqual(details["exceptions"][0]["exceptions"][0]["type"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
