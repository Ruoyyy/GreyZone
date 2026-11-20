from contextlib import nullcontext
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from support import BASELINE, RUN_ID, fixture, change
import experiment_common as C
import environment
import plan
import run
import run_all
import diagnostics
import summarize
from process import execute
from progress import Progress


def arguments(**values):
    return SimpleNamespace(**{**{key: None for key in C.DEFAULTS}, "fresh_reset": False,
        "baseline": "clean", "repeats": 1, "scenario": ["private-pod-leak"], "status_interval": 0.05, **values})


class OrchestrationTests(unittest.TestCase):
    def test_manifest_and_launch_commands_use_scenario_budget(self):
        for framework in C.FRAMEWORKS:
            args = arguments()
            manifest = run.manifest_for(args, framework, provenance={"fixture": True}, reference=BASELINE)
            self.assertEqual(manifest["scenario_specs"]["private-pod-leak"]["maxTurns"], 10)
            self.assertNotIn("max_turns", manifest["options"])
            command = run.command(manifest, "private-pod-leak")
            self.assertNotIn("--max-turns", command)
            self.assertEqual(command[command.index("--oracle-wait") + 1], "60")

    def test_resume_freezes_every_execution_budget(self):
        for key in C.DEFAULTS:
            args = arguments(**{key: C.DEFAULTS[key] + 1})
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "Resume cannot change"):
                C.options(args, C.DEFAULTS)
        with self.assertRaises(ValueError):
            C.options(arguments(oracle_wait=60, collection_timeout=60))
        for value in ("nan", "inf", "0", "-1"):
            with self.assertRaises(ValueError):
                C.positive(value)

    def test_check_never_restores_creates_a_plan_or_calls_models(self):
        for module, argv in ((run, ["--framework", "pyrit", "--check"]), (run_all, ["--check"])):
            with self.subTest(module=module.__name__), patch.object(module, "run_lock", return_value=nullcontext(99)), \
                 patch.object(module, "preflight") as preflight, patch.object(module, "execute") as launch, \
                 patch.object(module.baseline, "verify") as verify, patch.object(module.plan, "snapshot") as snapshot:
                self.assertEqual(module.main(argv), 0)
                preflight.assert_called_once()
                launch.assert_not_called()
                verify.assert_not_called()
                snapshot.assert_not_called()

    def test_cli_rejects_turn_budget_override(self):
        with self.assertRaises(SystemExit) as result:
            run.main(["--framework", "pyrit", "--max-turns", "1"])
        self.assertEqual(result.exception.code, 2)

    def test_baseline_capture_logs_stay_outside_results(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch.object(run, "ROOT", root), patch.object(run, "RESULTS", root / "results"), \
                 patch.object(run, "run_lock", return_value=nullcontext(99)), \
                 patch.object(run, "preflight"), patch.object(run, "execute") as launch:
                self.assertEqual(run.main(["--save-baseline", "clean"]), 0)
            self.assertFalse((root / "results").exists())
            self.assertEqual(launch.call_args.args[4].path, root / ".run/baseline-capture.log")
            self.assertEqual(launch.call_args.args[5], root / ".run/baseline-capture-console.log")

    def test_source_fingerprint_includes_uncommitted_typescript_not_runtime_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "backend").mkdir()
            source = root / "backend/new.ts"
            source.write_text("first version")
            with patch.object(C, "ROOT", root):
                first = C.configuration()
                source.write_text("second version")
                second = C.configuration()
                (root / "backend/node_modules").mkdir()
                (root / "backend/node_modules/ignore.ts").write_text("ignored")
                third = C.configuration()
            self.assertNotEqual(first, second)
            self.assertEqual(second, third)

    def test_runtime_or_model_drift_is_rejected(self):
        saved = {"revision": "same", "configuration": {}, "images": {}, "runtimes": {"pyrit": {"python": "3.11"}}, "victim_model": "first"}
        current = {**saved, "victim_model": "second"}
        with patch.object(plan, "snapshot", return_value=current), self.assertRaisesRegex(ValueError, "victim_model"):
            plan.verify(saved, ["pyrit"])

    def test_failed_or_uncertain_framework_can_never_be_finalized(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            _, row = fixture(path)
            marker = path / "framework/native/completion.json"
            change(marker, lambda d: d.update(oracleStatus="error"))
            self.assertTrue(run.can_finalize(path, row))
            row["execution_error"] = "Interrupted"
            self.assertFalse(run.can_finalize(path, row))
            row.pop("execution_error")
            change(path / "framework/native/attack-state.json", lambda d: d["events"][0].update(status="sending"))
            self.assertFalse(run.can_finalize(path, row))

    def test_attempt_restores_then_invokes_one_runner_and_archives(self):
        with tempfile.TemporaryDirectory() as folder:
            batch = Path(folder)
            manifest, _ = fixture(batch / "fixture")
            import shutil
            shutil.rmtree(batch / "fixture")
            manifest.update(provenance={"revision": "fixture-revision", "attacker_model": "fixture-model", "victim_model": "fixture-model", "oracle_policy": "2"}, reset_mode="baseline")
            C.write_json(batch / "manifest.json", manifest)
            args = arguments()
            C.options(args)
            calls = []
            def launch(command, cwd, env, descriptor, progress, log, timeout, **kwargs):
                calls.append(command)
                self.assertEqual(descriptor, 99)
                if len(calls) == 1:
                    self.assertIn("restore", command)
                    self.assertEqual(timeout, 1200)
                    self.assertIn("COMMONLY_RESET_DEADLINE", env)
                    return 0
                path = log.parent
                _, existing = fixture(path)
                run_id = env["COMMONLY_EXPERIMENT_RUN_ID"]
                change(path / "framework/native/config.json", lambda d: d.update(runId=run_id))
                change(path / "oracle/episode.json", lambda d: d["redteam"].update(runId=run_id))
                change(path / "oracle/result.json", lambda d: d["evaluation"].update(
                    runId=run_id, startedAt=C.now(), closedAt=C.now()))
                return 0
            with patch.object(run.plan, "verify"), patch.object(run, "execute", side_effect=launch), \
                 patch.object(diagnostics, "collect"), patch.object(diagnostics, "optional_diagnostics"):
                valid, pending = run.attempt(batch, manifest, "private-pod-leak", 1, args, 99)
            self.assertTrue(valid)
            self.assertFalse(pending)
            self.assertEqual(len(calls), 2)
            self.assertNotIn("--max-turns", calls[1])

    def test_collect_only_never_restores_or_starts_framework(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            batch = root / "pyrit/batch"
            manifest, row = fixture(batch / "private-pod-leak/repeat-01/attempt-01")
            C.write_json(batch / "manifest.json", manifest)
            with patch.object(run, "RESULTS", root), patch.object(run, "run_lock", return_value=nullcontext(99)), \
                 patch.object(run, "preflight") as preflight, patch.object(run, "execute") as launch, \
                 patch.object(diagnostics, "collect"), patch.object(diagnostics, "optional_diagnostics"):
                self.assertEqual(run.main(["--framework", "pyrit", "--resume", "batch", "--collect-only"]), 0)
                launch.assert_not_called()
                preflight.assert_not_called()

    def test_unresponsive_docker_container_queries_fail_before_reset(self):
        with patch.object(environment, "assert_no_watcher"), \
             patch.object(environment.shutil, "which", return_value="docker"), \
             patch.object(environment.subprocess, "run", side_effect=subprocess.TimeoutExpired("docker ps", 20)), \
             patch.object(environment, "images") as images:
            with self.assertRaisesRegex(RuntimeError, "Docker container queries"):
                environment.preflight()
            images.assert_not_called()

    def test_resume_skips_valid_repetitions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            batch = root / "pyrit/batch"
            path = batch / "private-pod-leak/repeat-01/attempt-01"
            manifest, row = fixture(path)
            manifest.update(reset_mode="baseline")
            C.write_json(batch / "manifest.json", manifest)
            summarize.classify(path, row, manifest)
            with patch.object(run, "RESULTS", root), patch.object(run, "run_lock", return_value=nullcontext(99)), \
                 patch.object(run, "preflight"), patch.object(run.plan, "verify"), \
                 patch.object(run.baseline, "verify", return_value=BASELINE), patch.object(run, "attempt") as attempt:
                self.assertEqual(run.main(["--framework", "pyrit", "--resume", "batch"]), 0)
                attempt.assert_not_called()

    def test_finalization_recovery_retains_original_returncode_and_never_resets(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "batch/private-pod-leak/repeat-01/attempt-01"
            manifest, row = fixture(path)
            row["returncode"] = 1
            C.write_json(path / "run.json", row)
            config = {"oracle/a.py": "hash"}
            manifest["provenance"].update(configuration=config, runtimes={"pyrit": {"python": "3.11"}})
            C.write_json(root / "batch/manifest.json", manifest)
            live = root / "live"
            C.write_json(live / "episode.json", C.read_json(path / "oracle/episode.json"))
            marker = path / "framework/native/completion.json"
            change(marker, lambda d: d.update(oracleStatus="error"))
            def finalize(command, *args, **kwargs):
                self.assertIn("redteam.finalize", command)
                self.assertNotIn("restore", command)
                change(marker, lambda d: d.update(oracleStatus="completed"))
                return 0
            with patch.object(run, "ORACLE", live), patch.object(run, "configuration", return_value=config), \
                 patch.object(run.plan, "runtime", return_value={"python": "3.11"}), \
                 patch.object(diagnostics, "native_path", return_value=path / "framework/native"), \
                 patch.object(diagnostics, "collect"), patch.object(diagnostics, "optional_diagnostics"), \
                 patch.object(run, "execute", side_effect=finalize) as execute:
                self.assertTrue(run.recover(root / "batch", manifest, arguments(), 99))
                self.assertEqual(execute.call_count, 1)
            recovered = C.read_json(path / "run.json")
            self.assertEqual(recovered["returncode"], 1)
            self.assertEqual(recovered["framework_returncode"], 0)
            self.assertEqual(recovered["duration_clock"], "wall_clock_recovery")
            self.assertTrue(recovered["recovery_history"])

    def test_pending_collection_stops_series_before_next_framework(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = {"framework": "promptfoo", "scenarios": ["private-pod-leak"], "repeats": 1,
                    "options": C.DEFAULTS, "provenance": {}, "reset_mode": "fresh", "baseline": None}
            with patch.object(run_all, "RESULTS", root), patch.object(run_all, "run_lock", return_value=nullcontext(99)), \
                 patch.object(run_all, "preflight"), patch.object(run_all, "manifest_for", return_value=data), \
                 patch.object(run_all.plan, "snapshot", return_value={}), patch.object(run_all.plan, "verify"), \
                 patch.object(run_all, "execute", return_value=75) as launch:
                self.assertEqual(run_all.main(["--fresh-reset"]), 75)
            self.assertEqual(launch.call_count, 1)
            self.assertIsNone(launch.call_args.args[4].path)
            self.assertIsNone(launch.call_args.args[5])
            self.assertEqual(len(list(root.iterdir())), 1)
            series = C.read_json(next(root.glob("series-*.json")))
            self.assertEqual(series["status"], "needs_collection")

    def test_series_continues_after_ordinary_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data = {"framework": "promptfoo", "scenarios": ["private-pod-leak"], "repeats": 1,
                    "options": C.DEFAULTS, "provenance": {}, "reset_mode": "fresh", "baseline": None}
            with patch.object(run_all, "RESULTS", root), patch.object(run_all, "run_lock", return_value=nullcontext(99)), \
                 patch.object(run_all, "preflight"), patch.object(run_all, "manifest_for", return_value=data), \
                 patch.object(run_all.plan, "snapshot", return_value={}), patch.object(run_all.plan, "verify"), \
                 patch.object(run_all, "execute", side_effect=[1, 0, 0, 0]) as execute:
                self.assertEqual(run_all.main(["--fresh-reset"]), 1)
                self.assertEqual(execute.call_count, 4)
            self.assertEqual(C.read_json(next(root.glob("series-*.json")))["status"], "incomplete")

    def test_process_streams_without_archiving_wrapper_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = io.StringIO()
            progress = Progress(None, output=output)
            progress.start()
            try:
                progress.stage("framework-batch", "pyrit")
                code = execute([sys.executable, "-c", "print('candidate output', end='', flush=True)"],
                    root, os.environ.copy(), None, progress, None, 3)
                progress.heartbeat()
            finally:
                progress.stop()
            self.assertEqual(code, 0)
            self.assertIn("candidate output", output.getvalue())
            self.assertIn("[status]", output.getvalue())
            self.assertEqual(list(root.iterdir()), [])

    def test_process_streams_output_without_newline_and_stops_on_timeout(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = io.StringIO()
            progress = Progress(root / "experiment.log", interval=0.02, output=output)
            progress.start()
            try:
                code = execute([sys.executable, "-c", "import sys;sys.stdout.write('without newline');sys.stdout.flush()"],
                    root, os.environ.copy(), None, progress, root / "console.log", 3)
                self.assertEqual(code, 0)
                self.assertEqual((root / "console.log").read_text(), "without newline")
                with self.assertRaises(TimeoutError):
                    execute([sys.executable, "-c", "import time;time.sleep(30)"], root, os.environ.copy(), None,
                            progress, root / "timeout.log", 0.1)
            finally:
                progress.stop()
            self.assertIn("without newline", output.getvalue())

    def test_sigterm_uses_interrupt_cleanup(self):
        with self.assertRaises(KeyboardInterrupt):
            run.terminate(15, None)

    def test_timeout_stops_descendants_in_separate_sessions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            progress = Progress(root / "experiment.log", interval=0.05, output=io.StringIO())
            progress.start()
            code = ("import subprocess,sys,time;"
                    "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True);"
                    "print(p.pid,flush=True);time.sleep(30)")
            try:
                with self.assertRaises(TimeoutError):
                    execute([sys.executable, "-c", code], root, os.environ.copy(), None, progress, root / "console.log", 0.3)
                pid = int((root / "console.log").read_text().strip())
                import time
                for _ in range(20):
                    stat = Path(f"/proc/{pid}/stat")
                    if not stat.exists() or stat.read_text().rpartition(") ")[2].split()[0] == "Z":
                        break
                    time.sleep(0.05)
                else:
                    self.fail("Descendant survived the command timeout")
            finally:
                progress.stop()
