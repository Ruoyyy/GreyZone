"""Target execution failures must not be scored as normal target replies."""
import io
import importlib.util
import json
import sys
import tempfile
import urllib.error
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from diagnostics import archive_diagnostics, target_errors
from experiment_common import write_json
from redteam.target_runtime import RunStateQueryError, failure_reply, intake_run_ids, read_states
import summarize

RUN_ID = "00000000-0000-0000-0000-000000000001"
ERROR_REPLY = ("\u4efb\u52a1\u6267\u884c\u672a\u5b8c\u6210\u3002\n"
               "\u9519\u8bef\uff1aprotocol repair failed\n"
               "\u9519\u8bef\u7801\uff1amodel_tool_protocol_violation\nRun ID\uff1a" + RUN_ID)


class RuntimeTests(unittest.TestCase):
    def test_transient_checkpoint_conflicts_retry_only_scoped_gets(self):
        lab = {"group_id": RUN_ID, "session_id": RUN_ID}
        def response(value):
            return io.BytesIO(json.dumps(value).encode())
        def conflict(code):
            return urllib.error.HTTPError("http://test/runs/run", 409, "Conflict", {},
                                          response({"detail": code}))
        replies = [response({"access_token": "test"}), conflict("inconsistent_checkpoint"),
                   conflict("checkpoint_not_found"), response({"run_id": RUN_ID, "status": "completed"})]
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict("os.environ", {"CLAWITH_EXPERIMENT_EVENTS": str(Path(directory)/"events.jsonl")}), \
                 patch("urllib.request.urlopen", side_effect=replies) as fetch, \
                 patch("redteam.target_runtime.time.sleep") as sleep:
                self.assertEqual(read_states(lab, [RUN_ID])[0]["status"], "completed")
            methods = [call.args[0].get_method() for call in fetch.call_args_list]
            self.assertEqual(methods, ["POST", "GET", "GET", "GET"])
            self.assertEqual(sleep.call_count, 2)
            rows = [json.loads(line) for line in (Path(directory)/"state-queries.jsonl").read_text().splitlines()]
            self.assertEqual([r["kind"] for r in rows], ["http_error", "http_error", "recovered"])
            self.assertEqual(rows[0]["detail"], "inconsistent_checkpoint")

    def test_persistent_or_nonretryable_conflicts_preserve_error_details(self):
        lab = {"group_id": RUN_ID, "session_id": RUN_ID}
        for status, detail, attempts in ((409, "inconsistent_checkpoint", 6),
                                         (409, "checkpoint_identity_mismatch", 1),
                                         (409, "unknown_conflict", 1), (403, "not_authorized", 1)):
            with self.subTest(status=status, detail=detail):
                replies = [io.BytesIO(b'{"access_token":"test"}')] + [
                    urllib.error.HTTPError("http://test/runs/run", status, "Error", {},
                        io.BytesIO(json.dumps({"detail": detail}).encode())) for _ in range(attempts)]
                with patch("urllib.request.urlopen", side_effect=replies) as fetch, \
                     patch("redteam.target_runtime.time.sleep"):
                    with self.assertRaises(RunStateQueryError) as raised:
                        read_states(lab, [RUN_ID])
                self.assertEqual(fetch.call_count, attempts+1)
                self.assertEqual(raised.exception.detail["http_status"], status)
                self.assertEqual(raised.exception.detail["detail"], detail)
                self.assertFalse(raised.exception.detail["retry"])

    def test_conflict_retries_respect_elapsed_deadline(self):
        replies = [io.BytesIO(b'{"access_token":"test"}'),
                   urllib.error.HTTPError("http://test/run", 409, "Conflict", {},
                      io.BytesIO(b'{"detail":"inconsistent_checkpoint"}'))]
        with patch("urllib.request.urlopen", side_effect=replies), \
             patch("redteam.target_runtime.time.monotonic", side_effect=[0, 24.8, 24.8]), \
             patch("redteam.target_runtime.time.sleep") as sleep:
            with self.assertRaises(RunStateQueryError):
                read_states({"group_id": RUN_ID, "session_id": RUN_ID}, [RUN_ID])
            sleep.assert_not_called()

    def test_group_at_rejects_self_but_allows_clearing_and_other_targets(self):
        source = Path(__file__).resolve().parents[2] / "backend/app/services/agent_runtime/group_at.py"
        spec = importlib.util.spec_from_file_location("group_at_contract", source)
        group_at = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(group_at)
        other = "00000000-0000-0000-0000-000000000002"
        for ids in ([RUN_ID], [other, RUN_ID]):
            with self.assertRaisesRegex(group_at.GroupAtArgumentsError, "cannot target yourself"):
                group_at.parse_group_at_participant_ids({"participant_ids": ids}, current_participant_id=RUN_ID)
        self.assertEqual(group_at.parse_group_at_participant_ids({"participant_ids": []}, current_participant_id=RUN_ID), ())
        self.assertEqual(group_at.parse_group_at_participant_ids({"participant_ids": [other]}, current_participant_id=RUN_ID), (other,))

    def test_failure_envelope_is_anchored_and_keeps_error_code(self):
        self.assertEqual(failure_reply(ERROR_REPLY), {"code": "model_tool_protocol_violation", "run_id": RUN_ID})
        self.assertIsNone(failure_reply("An explanation quoting:\n" + ERROR_REPLY))
        self.assertIsNone(failure_reply("I refuse this request."))

    def test_intake_requires_run_identity(self):
        self.assertEqual(intake_run_ids({"run_ids": [RUN_ID, RUN_ID]}), [RUN_ID])
        for receipt in ({}, {"run_ids": []}, {"error_code": "dispatch_failed"}):
            with self.assertRaises(RuntimeError):
                intake_run_ids(receipt)

    def test_state_queries_are_scoped_and_validate_identity(self):
        responses = [{"access_token": "test"}, {"run_id": RUN_ID, "status": "failed"}]

        def request(*_args, **_kwargs):
            return io.BytesIO(json.dumps(responses.pop(0)).encode())

        lab = {"group_id": RUN_ID, "session_id": RUN_ID}
        with patch("urllib.request.urlopen", side_effect=request) as fetch:
            self.assertEqual(read_states(lab, [RUN_ID]), [{"run_id": RUN_ID, "status": "failed"}])
            self.assertIn(f"/groups/{RUN_ID}/sessions/{RUN_ID}/runs/{RUN_ID}", fetch.call_args.args[0].full_url)
        responses.extend([{"access_token": "test"}, {"run_id": "wrong", "status": "completed"}])
        with patch("urllib.request.urlopen", side_effect=request):
            with self.assertRaisesRegex(RuntimeError, "identity"):
                read_states(lab, [RUN_ID])

    def test_historical_error_then_success_is_invalid_with_blank_score(self):
        events = [{"kind": "result", "reply": ERROR_REPLY, "status": "continue", "oracleSuccess": False},
                  {"kind": "result", "reply": "Leaked data", "status": "oracle-success", "oracleSuccess": True}]
        self.assertEqual(len(target_errors(events)), 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
            run = summarize.classify(path, {"score": 1, "framework": "deepteam"})
            self.assertEqual(run["status"], "invalid")
            self.assertEqual(run["target_error_count"], 1)
            self.assertEqual(run["target_error_codes"], "model_tool_protocol_violation")
            self.assertNotIn("score", run)

    def test_failed_state_is_an_error_even_with_an_ordinary_reply(self):
        events = [{"kind": "result", "reply": "Done", "target_runs": [{"run_id": RUN_ID, "status": "failed"}]}]
        self.assertEqual(target_errors(events), [{"code": "target_failed", "run_id": RUN_ID}])
        quoted = [{"kind": "result", "reply": ERROR_REPLY,
                   "target_runs": [{"run_id": RUN_ID, "status": "completed"}]}]
        self.assertEqual(target_errors(quoted), [])

    def test_diagnostics_exports_exact_runs_without_touching_target_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "events.jsonl").write_text(json.dumps({"kind": "result", "reply": ERROR_REPLY}) + "\n")
            result = type("Result", (), {"stdout": '{"runs": []}\n'})()
            with patch("diagnostics.subprocess.run", return_value=result) as execute:
                archive_diagnostics(path, "2026-11-17T00:00:00+00:00")
            command = execute.call_args_list[0].args[0]
            self.assertEqual(command[-1], RUN_ID)
            self.assertEqual(command[:7], ["docker", "compose", "exec", "-T", "backend", "python", "-c"])
            self.assertTrue((path / "target-runtime.json").exists())


if __name__ == "__main__":
    unittest.main()
