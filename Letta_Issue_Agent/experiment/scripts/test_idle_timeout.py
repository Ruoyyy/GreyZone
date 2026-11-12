"""Check configurable Oracle inactivity windows without Docker or model calls."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import run
from common import read_json, write_json

SCENARIO = "runtime-credential-disclosure"


class IdleTimeoutTests(unittest.TestCase):
    def run_batch(self, override=None, existing=None):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            argv = ["run.py", "--framework", "promptfoo", "--baseline", "clean",
                    "--scenario", SCENARIO, "--repeats", "1"]
            if override is not None:
                argv += ["--oracle-idle-timeout", str(override)]
            if existing is not None:
                manifest = {"framework": "promptfoo", "baseline": "clean", "repeats": 1,
                    "scenarios": [SCENARIO], "repo": "owner/repo", "web_url": "http://localhost",
                    "configuration_hashes": {}, **existing}
                write_json(root / "promptfoo/batch/manifest.json", manifest)
                argv += ["--resume", "batch"]
            with (patch("sys.argv", argv), patch.object(run, "RESULTS", root),
                  patch.object(run, "campaign_lock", return_value=contextlib.nullcontext(42)),
                  patch.object(run, "verify_baseline"), patch.object(run, "configuration_hashes", return_value={}),
                  patch.object(run, "run_once", return_value=True) as execute,
                  patch.object(run, "summarize"), contextlib.redirect_stdout(io.StringIO())):
                self.assertEqual(run.main(), 0)
            args = execute.call_args.args[0]
            manifest = read_json(next(root.glob("promptfoo/*/manifest.json")))
            return args.oracle_idle_timeout, manifest

    def test_new_batches_default_to_sixty_seconds(self):
        value, manifest = self.run_batch()
        self.assertEqual(value, 60)
        self.assertEqual(manifest["oracle_idle_timeout_seconds"], 60)

    def test_custom_window_is_recorded(self):
        value, manifest = self.run_batch(30)
        self.assertEqual(value, 30)
        self.assertEqual(manifest["oracle_idle_timeout_seconds"], 30)

    def test_resume_keeps_original_window(self):
        value, _ = self.run_batch(5, {"oracle_idle_timeout_seconds": 90})
        self.assertEqual(value, 90)
        legacy, _ = self.run_batch(5, {})
        self.assertIsNone(legacy)

    def test_nonpositive_window_is_rejected_before_accessing_environment(self):
        with (patch("sys.argv", ["run.py", "--oracle-idle-timeout", "0"]),
              patch.object(run, "campaign_lock") as lock,
              contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc):
            run.main()
        self.assertEqual(exc.exception.code, 2)
        lock.assert_not_called()

    def test_runner_receives_override_without_editing_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            batch = Path(temp)
            write_json(batch / "manifest.json", {"scenarios": [SCENARIO], "repeats": 1})
            args = SimpleNamespace(framework="promptfoo", baseline="clean", oracle_idle_timeout=30,
                                   status_interval=30, repo="owner/repo", web_url="http://localhost")
            with (patch.object(run, "restore_baseline", side_effect=RuntimeError("Stop before attack")) as restore,
                  patch.object(run, "output_snapshot", return_value={}),
                  patch.object(run.subprocess, "check_output", return_value="revision"),
                  contextlib.redirect_stdout(io.StringIO())):
                self.assertFalse(run.run_once(args, batch, SCENARIO, 1, 42))
            self.assertEqual(restore.call_args.args[1]["EPISODE_IDLE_TIMEOUT"], "30")
            metadata = read_json(next(batch.glob("*/repeat-*/attempt-*/run.json")))
            self.assertEqual(metadata["oracle_idle_timeout_seconds"], 30)


if __name__ == "__main__":
    unittest.main()
