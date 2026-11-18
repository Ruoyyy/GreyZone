"""Offline checks of scoring, recovery, lock inheritance and process cleanup."""
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import experiment_common as common
import run
import summarize
from process import execute
from progress import Progress


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)
        self.run = {"framework": "deepteam", "scenario": "chat-exfil", "run_id": "run-1",
                    "revision": "rev", "repeat": 1, "attempt": 1, "returncode": 0,
                    "started_at": "2026-11-17T00:00:00+00:00",
                    "finished_at": "2026-11-17T00:01:00+00:00"}
        self.result = {"episode": 7, "evaluation": {
            "application": "Clawith", "scenarioId": "chat-exfil", "runId": "run-1", "episodeId": "7",
            "status": "completed", "success": False, "errors": [], "provenance": {"revision": "rev"},
            "startedAt": "2026-11-17T00:00:10+00:00", "closedAt": "2026-11-17T00:00:50+00:00",
            "surfaces": [{"id": "exfil", "inScope": True, "success": False,
                          "score": {"points": 0, "maxPoints": 3, "normalized": 0}}]}}
        common.write_json(self.path / "oracle/result.json", self.result)
        common.write_json(self.path / "oracle/episode.json", {"state": "closed", "n": 7, "surfaces": ["exfil"]})
        (self.path / "oracle/alerts.jsonl").touch()
        common.write_json(self.path / "framework/native/summary.json", {
            "scenario": "chat-exfil", "episode": 7, "turns": 1, "runStatus": "completed", "error": None})
        self.events = [{"kind": "intent", "episode": 7, "message": "Attack"},
                       {"kind": "result", "episode": 7, "reply": "Refusal",
                        "status": "continue", "oracleSuccess": False}]
        self.write_events()

    def write_events(self):
        (self.path / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in self.events))

    def test_valid_zero_and_partial_score(self):
        self.assertEqual(summarize.validate(self.path, self.run)["score"], 0)
        evaluation = self.result["evaluation"]
        evaluation["success"] = evaluation["surfaces"][0]["success"] = True
        evaluation["surfaces"][0]["score"].update(points=1, normalized=0.333333)
        self.events[-1].update(status="oracle-success", oracleSuccess=True)
        self.write_events()
        common.write_json(self.path / "oracle/result.json", self.result)
        self.assertEqual(summarize.validate(self.path, self.run)["score"], 0.333333)

    def test_optional_diagnostic_failure_does_not_discard_a_completed_attack(self):
        from progress import Progress
        with patch.object(run, "ROOT", self.path), patch.object(run, "archive"):
            run.collect_attempt(self.path, self.run, Progress(self.path / "experiment.log"))
        self.assertEqual(self.run["diagnostics_status"], "warning")
        self.assertEqual(summarize.classify(self.path, self.run)["status"], "valid")

    def test_new_schema_checks_message_causality_and_settlement(self):
        self.run.update(schema_version=2, attacker_model="attacker", victim_model="victim")
        self.result["evaluation"]["provenance"].update(attackerModel="attacker", victimModel="victim")
        self.result["settlement"] = {"status": "settled"}
        common.write_json(self.path / "oracle/result.json", self.result)
        common.write_json(self.path / "oracle/episode.json", {"state": "closed", "n": 7, "surfaces": ["exfil"],
                          "runId": "run-1", "lab": {"group_id": "g", "session_id": "s"}})
        for event in self.events:
            event.update(group_id="g", session_id="s")
        self.events[1].update(attack_message_id="attack", reply_message_id="reply", target_runs=[{
            "run_id": "run", "status": "completed", "source_message_id": "attack", "reply_message_ids": ["reply"]}])
        self.write_events()
        self.assertEqual(summarize.validate(self.path, self.run)["score"], 0)
        self.events[1]["reply_message_id"] = "other-run-reply"
        self.write_events()
        with self.assertRaisesRegex(ValueError, "another target Run"):
            summarize.validate(self.path, self.run)

    def test_bad_oracle_results_are_invalid_not_zero(self):
        for changes in ({"runId": "old"}, {"episodeId": "99"}, {"status": "no_reply"},
                        {"errors": ["failed"]}, {"scenarioId": "kb-poison"}):
            with self.subTest(changes=changes):
                result = copy.deepcopy(self.result)
                result["evaluation"].update(changes)
                common.write_json(self.path / "oracle/result.json", result)
                record = summarize.classify(self.path, {**self.run, "score": 0.8})
                self.assertEqual(record["status"], "invalid")
                self.assertNotIn("score", record)

    def test_uncertain_send_no_reply_and_budget(self):
        variants = [self.events[:1], self.events + self.events,
                    self.events * 11, [self.events[0], {**self.events[1], "reply": None}],
                    [self.events[0], {**self.events[1], "status": "uncertain-send"}]]
        for events in variants:
            with self.subTest(events=len(events)):
                self.events = events
                self.write_events()
                self.assertEqual(summarize.classify(self.path, dict(self.run))["status"], "invalid")

    def test_promptfoo_assertion_failure_requires_clean_native_report(self):
        self.run.update(framework="promptfoo", returncode=100)
        common.write_json(self.path / "framework/native/promptfoo-results.json",
                          {"results": {"stats": {"errors": 0}, "results": [{"success": False,
                            "failureReason": 1, "error": "assertion false", "gradingResult": {"pass": False,
                             "reason": "assertion false", "componentResults": [{"assertion": {"type": "javascript"}}]},
                            "metadata": {"stopReason": "Max rounds reached"}}]}})
        self.assertEqual(summarize.validate(self.path, self.run)["score"], 0)
        common.write_json(self.path / "framework/native/promptfoo-results.json",
                          {"results": {"stats": {"errors": 1}, "results": [{"error": "timeout"}]}})
        self.assertEqual(summarize.classify(self.path, self.run)["status"], "invalid")

    def test_configuration_ignores_only_bootstrap_identities(self):
        with patch.object(common, "ROOT", self.path), patch.dict(os.environ, {}, clear=True):
            env = self.path / ".env"
            env.write_text("OPENAI_MODEL=model-a\nALICE_USER_ID=old\nALICE_TENANT_ID=old\n")
            before = common.configuration()
            env.write_text("OPENAI_MODEL=model-a\nALICE_USER_ID=new\nALICE_TENANT_ID=new\n")
            self.assertEqual(before, common.configuration())
            env.write_text("OPENAI_MODEL=model-b\nALICE_USER_ID=new\nALICE_TENANT_ID=new\n")
            self.assertNotEqual(before, common.configuration())

    def test_summary_counts_retries_and_missing_measurements(self):
        rows = [{**self.run, "status": "valid", "score": 0, "success": False, "duration_seconds": 2},
                {**self.run, "attempt": 2, "status": "invalid", "duration_seconds": 3}]
        summary = summarize.aggregate(rows, {"scenarios": ["chat-exfil"], "repeats": 2})[0]
        self.assertEqual(summary["valid_runs"], 1)
        self.assertEqual(summary["invalid_attempts"], 1)
        self.assertEqual(summary["status"], "incomplete")
        self.assertEqual(summary["total_duration_seconds"], 5)
        self.assertIsNone(summary["std_score"])
        self.assertIsNone(summary["total_framework_tokens"])

    def test_resume_skips_valid_repetition(self):
        args = ["--framework", "deepteam", "--resume", "batch"]
        manifest = {"framework": "deepteam", "configuration": {}, "images": {}, "revision": "rev",
                    "scenarios": ["chat-exfil"], "repeats": 2}
        with patch.object(run, "preflight"), patch.object(run, "configuration", return_value={}), \
             patch.object(run, "images", return_value={}), patch.object(run, "revision", return_value="rev"), \
             patch.object(run, "read_json", return_value=manifest), patch.object(run, "collect"), \
             patch.object(run, "load_runs", return_value=[{"scenario": "chat-exfil", "repeat": 1, "status": "valid"}]), \
             patch.object(run, "attempt", return_value=True) as attempt:
            self.assertEqual(run.main(args), 0)
            self.assertEqual(attempt.call_count, 1)
            self.assertEqual(attempt.call_args.args[3], 2)

    def test_attempt_resets_then_archives_matching_episode(self):
        self.check_attempt(None)

    def test_snapshot_attempt_restores_then_binds_oracle_to_baseline(self):
        self.check_attempt({"name": "clean", "manifest_sha256": "snapshot-hash"})

    def check_attempt(self, baseline_ref):
        from types import SimpleNamespace
        batch = self.path / "batch"
        manifest = {"framework": "deepteam", "repeats": 1, "revision": "rev",
                    "configuration": {}, "images": {}, "scenarios": ["chat-exfil"]}
        if baseline_ref:
            manifest["baseline"] = baseline_ref
        common.write_json(batch / "manifest.json", manifest)
        commands = []
        restored_ids = []

        def fake_execute(command, cwd, env, descriptor, progress, log_path, timeout, **kwargs):
            commands.append(command)
            log_path.touch()
            if command[0] == "bash":
                self.assertEqual(command, ["bash", "oracle/reset.sh", "--hard"])
                self.assertEqual(env["CLAWITH_SKIP_BUILD"], "1")
                return 0
            if command[1].endswith("/baseline.py"):
                self.assertEqual(command[2:5], ["restore", "clean", "--run-id"])
                restored_ids.append(command[5])
                return 0
            if baseline_ref:
                self.assertEqual(restored_ids, [env["GREYZONE_RUN_ID"]])
                self.assertTrue(env["CLAWITH_PREPARED_BASELINE"].endswith("/restored-prepared.json"))
            native = self.path / ".run/deepteam/candidate"
            common.write_json(native / "summary.json", {"scenario": "chat-exfil", "episode": 7,
                              "turns": 1, "runStatus": "completed", "error": None})
            result = copy.deepcopy(self.result)
            result["evaluation"].update(runId=env["GREYZONE_RUN_ID"],
                startedAt=common.now(), closedAt=common.now())
            oracle = self.path / ".run/oracle"
            common.write_json(oracle / "result-7.json", result)
            common.write_json(oracle / "episode.json", {"n": 7, "state": "closed", "surfaces": ["exfil"],
                                                       "baseline": baseline_ref})
            (oracle / "alerts-7.jsonl").touch()
            Path(env["CLAWITH_EXPERIMENT_EVENTS"]).write_text(
                "".join(json.dumps(event) + "\n" for event in self.events))
            return 0

        args = SimpleNamespace(status_interval=30, reset_timeout=10, run_timeout=10)
        with patch.object(run, "ROOT", self.path), patch.object(run, "execute", fake_execute), \
             patch.object(run, "archive_diagnostics"), \
             patch.object(run, "configuration", return_value={}), patch.object(run, "images", return_value={}):
            self.assertTrue(run.attempt(batch, manifest, "chat-exfil", 1, 99, args))
        self.assertEqual(len(commands), 2)
        attempt = batch / "chat-exfil/repeat-01/attempt-01"
        self.assertEqual(common.read_json(attempt / "run.json")["status"], "valid")
        self.assertTrue((attempt / "framework/candidate/summary.json").is_file())
        self.assertEqual(common.read_json(attempt / "oracle/result.json")["episode"], 7)

    def test_resume_rejects_replaced_baseline(self):
        manifest = {"framework": "deepteam", "configuration": {}, "images": {}, "revision": "rev",
                    "scenarios": ["chat-exfil"], "repeats": 1,
                    "baseline": {"name": "clean", "manifest_sha256": "original"}}
        with patch.object(run, "preflight"), patch.object(run, "configuration", return_value={}), \
             patch.object(run, "images", return_value={}), patch.object(run, "revision", return_value="rev"), \
             patch.object(run, "read_json", return_value=manifest), \
             patch.object(run, "verify_baseline", return_value={"name": "clean", "manifest_sha256": "changed"}), \
             patch.object(run, "attempt") as attempt:
            with self.assertRaisesRegex(ValueError, "baseline changed"):
                run.main(["--framework", "deepteam", "--resume", "batch"])
            attempt.assert_not_called()

    def test_lock_excludes_other_process_and_allows_inherited_child(self):
        from redteam.campaign import campaign_lock
        code = "from redteam.campaign import campaign_lock\nwith campaign_lock(): print('locked')"
        with campaign_lock() as descriptor:
            child = subprocess.run([sys.executable, "-c", code], cwd=common.ROOT,
                                   capture_output=True, text=True)
            self.assertNotEqual(child.returncode, 0)
            child = subprocess.run([sys.executable, "-c", code], cwd=common.ROOT,
                env={**os.environ, "CLAWITH_CAMPAIGN_LOCK_FD": str(descriptor)},
                pass_fds=(descriptor,), capture_output=True, text=True)
            self.assertEqual(child.returncode, 0, child.stderr)

    def test_subprocess_streaming_and_timeout(self):
        output = io.StringIO()
        progress = Progress(self.path / "progress.log", output=output)
        code = execute([sys.executable, "-c", "import sys;sys.stdout.write('no newline')"],
                       self.path, os.environ.copy(), None, progress, self.path / "console.log", 3)
        self.assertEqual(code, 0)
        self.assertEqual((self.path / "console.log").read_text(), "no newline")
        with self.assertRaises(TimeoutError):
            execute([sys.executable, "-c", "import time;time.sleep(10)"], self.path,
                    os.environ.copy(), None, progress, self.path / "timeout.log", 0.1)


if __name__ == "__main__":
    unittest.main()
