"""Tests of preflight failure reporting and process isolation, without Docker."""

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import check_deployment as check


class DeploymentPreflightTests(unittest.TestCase):
    def test_invalid_build_inputs_cannot_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {"services": {"app": {"build": {"context": str(root)}}}}
            self.assertEqual(check.build_inputs(config)[0], "FAIL")
            (root / "Dockerfile").write_text("FROM scratch\n")
            self.assertEqual(check.build_inputs(config)[0], "PASS")
            config["services"]["app"]["build"]["additional_contexts"] = {"source": str(root / "absent")}
            self.assertEqual(check.build_inputs(config)[0], "FAIL")

    def test_unresolved_build_input_is_blocked(self):
        config = {"services": {"app": {"build": {"context": "${SOURCE_ROOT}"}}}}
        self.assertEqual(check.build_inputs(config)[0], "BLOCKED")

    def test_missing_executable_and_failed_command_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory)
            self.assertEqual(check.run_command([str(cwd / "absent")], cwd, 2)[0], "BLOCKED")
            status, _, stderr, returncode = check.run_command(
                [sys.executable, "-c", "import sys; print('failure detail', file=sys.stderr); sys.exit(7)"], cwd, 2,
            )
            self.assertEqual(status, "FAIL")
            self.assertEqual(returncode, 7)
            self.assertIn("failure detail", stderr)

    def test_timeout_stops_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            started = time.monotonic()
            status, _, detail, _ = check.run_command(
                [sys.executable, "-c", "import time; time.sleep(30)"], Path(directory), 0.1,
            )
            self.assertEqual(status, "FAIL")
            self.assertIn("Timed out", detail)
            self.assertLess(time.monotonic() - started, 3)

    def test_requested_cwd_and_environment_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory).resolve()
            code = "import os,json; print(json.dumps([os.getcwd(), os.getenv('RUN_INTEGRATION_TESTS'), os.getenv('NODE_OPTIONS')]))"
            with patch.dict(os.environ, {"RUN_INTEGRATION_TESTS": "true", "NODE_OPTIONS": "--max-old-space-size=16384"}):
                status, stdout, _, _ = check.run_command([sys.executable, "-c", code], cwd, 2)
                self.assertEqual(status, "PASS")
                self.assertEqual(json.loads(stdout), [str(cwd), None, "--max-old-space-size=512"])
                self.assertEqual(os.environ["RUN_INTEGRATION_TESTS"], "true")

    def test_failure_propagates_and_later_checks_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "example").mkdir()
            ranges = {"example": {"directory": "example", "sources": ["missing-file"]}}
            report = root / "report.json"
            with patch.object(check, "ROOT", root), patch.object(check, "RANGES", ranges), \
                    patch.object(check.shutil, "which", return_value="/fake/tool"), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(check.main(["--report", str(report)]), 1)
            data = json.loads(report.read_text())
            self.assertEqual(data["status"], "FAIL")
            self.assertEqual([r["status"] for r in data["results"]], ["FAIL", "PASS", "NOT_RUN"])
            self.assertTrue(all(status == "NOT_RUN" for status in data["deployment"].values()))

    def test_project_selection_excludes_other_ranges(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "selected").mkdir()
            ranges = {name: {"directory": name} for name in ["selected", "absent"]}
            report = root / "report.json"
            with patch.object(check, "ROOT", root), patch.object(check, "RANGES", ranges), \
                    patch.object(check.shutil, "which", return_value="/fake/tool"), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(check.main(["--project", "selected", "--report", str(report)]), 0)
            self.assertEqual({r["project"] for r in json.loads(report.read_text())["results"]}, {"selected"})


if __name__ == "__main__":
    unittest.main()
