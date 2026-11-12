"""Exercise live output with real local processes, without models or Docker."""
import io
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from progress import Progress
from run import execute


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.output = io.StringIO()
        self.progress = Progress(self.path / "experiment.log", 0.04, self.output)
        self.lock = (self.path / "lock").open("w")
        self.addCleanup(self.lock.close)

    def execute(self, script, timeout=3):
        return execute([sys.executable, "-c", script], self.path, dict(os.environ),
                       self.path / "console.log", self.lock.fileno(), timeout, self.progress)

    def test_native_bytes_are_preserved_and_not_mixed_with_status(self):
        self.progress.stage("running-framework")
        script = ("import os,time; os.write(1,b'prefix-\\xe2'); time.sleep(0.02); "
                  "os.write(1,b'\\x82\\xac'); os.write(2,b'-stderr')")
        self.assertEqual(self.execute(script), 0)
        expected = b"prefix-\xe2\x82\xac-stderr"
        self.assertEqual((self.path / "console.log").read_bytes(), expected)
        self.assertIn(expected.decode(), self.output.getvalue())
        self.assertNotIn("prefix", (self.path / "experiment.log").read_text())

    def test_unterminated_output_is_forwarded_before_process_exit(self):
        ready = threading.Event()
        errors = []
        forward = self.progress.forward

        def observed(text):
            forward(text)
            if "ready" in text:
                ready.set()

        self.progress.forward = observed
        script = ("import pathlib,time; print('ready',end='',flush=True); "
                  "\nwhile not pathlib.Path('release').exists(): time.sleep(0.01)")

        def run_child():
            try:
                self.execute(script)
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=run_child)
        worker.start()
        try:
            self.assertTrue(ready.wait(2), "Output was held until newline or process exit")
            self.assertTrue(worker.is_alive())
            self.assertEqual((self.path / "console.log").read_text(), "ready")
        finally:
            (self.path / "release").touch()
            worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])

    def test_heartbeat_continues_during_silent_process(self):
        self.progress.stage("running-framework")
        self.progress.start()
        try:
            self.assertEqual(self.execute("import time; time.sleep(0.2)"), 0)
        finally:
            self.progress.stop()
        text = (self.path / "experiment.log").read_text()
        self.assertIn("[status] stage=running-framework", text)
        self.assertIn("process=running", text)
        self.assertIn("last_output=none", text)
        self.assertEqual((self.path / "console.log").read_bytes(), b"")

    def test_timeout_still_terminates_child(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.execute("import time; time.sleep(10)", timeout=0.1)
        self.assertIsNotNone(self.progress.process.poll())

    def test_heartbeat_covers_oracle_and_status_starts_on_new_line(self):
        self.progress.forward("partial")
        self.progress.stage("waiting-for-oracle", "issue=5 | timeout=1200s")
        self.progress.heartbeat()
        text = self.output.getvalue()
        self.assertIn("partial\n[", text)
        self.assertIn("stage=waiting-for-oracle", text)
        self.assertIn("issue=5", text)
        self.assertNotIn("partial", (self.path / "experiment.log").read_text())


if __name__ == "__main__":
    unittest.main()
