"""Usage, frozen plans, interruption handling and collection-only recovery."""
import asyncio
import json
import os
import signal
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import plan
import run
import run_all
from experiment_common import write_json, read_json
from usage import native_usage, combine
from redteam.usage import install_usage_hook
from redteam.finalize import close


class FeatureTests(unittest.TestCase):
    def test_finalization_recovery_preserves_oracle_policy_and_python(self):
        saved = {"oracle/watch.py": "original", "redteam/usage.py": "old"}
        row = {"run_id": "r", "revision": "rev", "provenance": {"configuration": saved,
               "runtimes": {"promptfoo": {"oracle_executable": "/pinned/python"}}}}
        with patch.object(run, "configuration", return_value={**saved, "redteam/usage.py": "new"}):
            self.assertEqual(run.finalization_environment(row)["UV_PYTHON"], "/pinned/python")
        with patch.object(run, "configuration", return_value={**saved, "oracle/watch.py": "changed"}):
            with self.assertRaisesRegex(ValueError, "Oracle code changed"):
                run.finalization_environment(row)

    def test_completion_survives_oracle_finalization_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            native = Path(directory) / ".run/pyrit/native"
            native.mkdir(parents=True)
            def fail(*args, **kwargs):
                marker = read_json(native / "completion.json")
                self.assertEqual(marker["framework_status"], "completed")
                raise OSError("Oracle is unavailable")
            with patch("redteam.finalize.subprocess.run", side_effect=fail):
                error = close(["unused"], {"GREYZONE_RUN_ID": "r"}, native, "chat-exfil", 1, True)
            self.assertIn("Oracle is unavailable", error)
            self.assertEqual(read_json(native / "completion.json")["oracle_status"], "error")

    def test_resume_freezes_every_execution_budget(self):
        args = types.SimpleNamespace(reply_timeout=60)
        budgets = plan.options(args)
        self.assertEqual(budgets["reply_timeout"], 60)
        restored = types.SimpleNamespace()
        self.assertEqual(plan.options(restored, budgets), budgets)
        with self.assertRaisesRegex(ValueError, "cannot change"):
            plan.options(types.SimpleNamespace(reply_timeout=61), budgets)

    def test_runtime_drift_is_rejected_before_a_later_framework(self):
        saved = {"revision": "r", "configuration": {}, "images": {}, "harness_python": "3",
                 "runtimes": {"pyrit": {"version": "old"}}}
        with patch.object(plan, "snapshot", return_value={**saved, "runtimes": {"pyrit": {"version": "new"}}}):
            with self.assertRaisesRegex(ValueError, "runtime"):
                plan.verify(saved, ["pyrit"])

    def test_sdk_hook_records_sync_async_usage_and_unknown_error_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "usage.jsonl"
            response = types.SimpleNamespace(id="response", model="model", usage=types.SimpleNamespace(
                model_dump=lambda: {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}))
            class Sync:
                def request(self, fail=False):
                    if fail:
                        raise RuntimeError("request failed")
                    return response
            class Async:
                async def request(self):
                    return response
            module = types.ModuleType("openai._base_client")
            module.AsyncAPIClient, module.SyncAPIClient = Async, Sync
            with patch.dict(sys.modules, {"openai": types.ModuleType("openai"), "openai._base_client": module}), \
                 patch.dict(os.environ, {"CLAWITH_EXPERIMENT_USAGE": str(file)}):
                install_usage_hook()
                install_usage_hook()
                self.assertIs(Sync().request(), response)
                self.assertIs(asyncio.run(Async().request()), response)
                with self.assertRaises(RuntimeError):
                    Sync().request(fail=True)
            rows = [json.loads(line) for line in file.read_text().splitlines()]
            measured = [r for r in rows if r["kind"] == "response"]
            self.assertEqual(len(measured), 3)
            self.assertEqual(measured[0]["total_tokens"], 12)
            self.assertIsNone(measured[-1]["total_tokens"])
            self.assertEqual(combine(measured, "test")["status"], "partial")

    def test_native_promptfoo_includes_generation_once_and_dreadnode_is_approximate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            write_json(path / "framework/native/promptfoo-results.json", {"results": {"stats": {"tokenUsage": {
                "total": 999, "attacker": {"prompt": 10, "completion": 2, "total": 12},
                "assertions": {"prompt": 1, "completion": 1, "total": 2}}}}})
            (path / "console.log").write_text("Observed generation token usage: 8 total (6 input, 2 output) across 1 request(s)\n")
            self.assertEqual(native_usage(path, "promptfoo")["total_tokens"], 22)
            (path / "framework/native/dreadnode.log").write_text("model · 4.9k tokens · 1 tool\n")
            usage = native_usage(path, "dreadnode")
            self.assertEqual(usage["total_tokens"], 4900)
            self.assertEqual(usage["status"], "approximate")

    def test_zero_calls_and_interrupted_usage_have_distinct_meanings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            journal = path / "framework/usage.jsonl"
            journal.parent.mkdir()
            journal.write_text('{"kind":"usage_start"}\n')
            self.assertEqual(native_usage(path, "pyrit")["total_tokens"], 0)
            with journal.open("a") as stream:
                stream.write('{"kind":"request_start","request_id":"r"}\n')
            usage = native_usage(path, "pyrit")
            self.assertEqual(usage["status"], "partial")
            self.assertIsNone(usage["total_tokens"])
            with journal.open("a") as stream:
                stream.write('{"kind":"response","request_id":"r","input_tokens":4,"output_tokens":2,"total_tokens":6}\n{')
            usage = native_usage(path, "pyrit")
            self.assertEqual(usage["status"], "partial")
            self.assertEqual(usage["total_tokens"], 6)

    def test_collect_only_never_resets_or_calls_models(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = root / "pyrit/batch"
            write_json(batch / "manifest.json", {})
            file = batch / "chat-exfil/repeat-01/attempt-01/run.json"
            write_json(file, {"framework": "pyrit", "scenario": "chat-exfil", "repeat": 1,
                "status": "invalid", "run_id": "run", "returncode": 0,
                "started_at": "2026-11-17T00:00:00+00:00", "collection_error": "copy failed"})
            def collect(path, row, progress):
                row.pop("collection_error")
                row["collection_status"] = "completed"
            args = types.SimpleNamespace(framework="pyrit", resume="batch", status_interval=30)
            with patch.object(run, "RESULTS", root), patch.object(run, "collect_attempt", side_effect=collect), \
                 patch.object(run, "classify"), patch.object(run, "collect", return_value=True), \
                 patch.object(run, "reset") as reset, patch.object(run, "execute") as execute:
                self.assertEqual(run.recover(args), 0)
                reset.assert_not_called()
                execute.assert_not_called()

    def test_sigterm_uses_the_same_cleanup_path_as_keyboard_interrupt(self):
        with self.assertRaises(KeyboardInterrupt):
            run.terminate(signal.SIGTERM, None)

    def test_series_forwards_termination_to_its_batch_process(self):
        child = types.SimpleNamespace(wait=Mock(side_effect=[KeyboardInterrupt(), 130]), send_signal=Mock())
        with patch.object(run_all.subprocess, "Popen", return_value=child):
            with self.assertRaises(KeyboardInterrupt):
                run_all.invoke(["unused"])
        child.send_signal.assert_called_once_with(signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
