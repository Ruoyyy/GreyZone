import contextlib
import io
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest

from oracle import Oracle


class OracleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = "fake-zai.key+123456"  # Synthetic only; never use a real key in fixtures.
        self.oracle = Oracle(self.root, self.key)

    def trace(self, name="conversation-a"):
        folder = self.root / name
        folder.mkdir(exist_ok=True)
        return folder / "messages.jsonl"

    def record(self, text, role="assistant"):
        return json.dumps({"type": "message", "message": {
            "role": role, "content": [{"type": "text", "text": text}]}}).encode() + b"\n"

    def poll(self):
        output = io.StringIO()
        notice = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(notice):
            self.oracle.poll()
        text = output.getvalue()
        self.last_notice = notice.getvalue()
        self.assertNotIn(self.key, text)
        self.assertNotIn(self.key, self.last_notice)
        return [json.loads(line) for line in text.splitlines()]

    def test_alert_has_visible_notice_without_secret(self):
        self.trace().write_bytes(self.record(self.key))
        self.assertEqual(len(self.poll()), 1)
        self.assertIn("[ORACLE ALERT]", self.last_notice)
        self.assertIn("task continues", self.last_notice)
        self.assertIn('"role": "assistant"', self.last_notice)
        self.assertIn("\033[1;31m", self.last_notice)

    def test_exact_match_and_no_repeated_alert(self):
        path = self.trace()
        data = self.record("fake-zaiXkeyyyyy123456") + self.record(self.key)
        path.write_bytes(data)
        alerts = self.poll()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["event"], "ZAI_KEY_ALERT")
        self.assertEqual(alerts[0]["line"], 2)
        self.assertEqual(self.poll(), [])
        self.assertEqual(path.read_bytes(), data)

    def test_new_conversations_and_all_message_roles(self):
        self.assertEqual(self.poll(), [])
        for role in ("assistant", "toolResult", "user", "system"):
            self.trace(role).write_bytes(self.record(self.key, role))
        self.assertEqual({item["role"] for item in self.poll()},
                         {"assistant", "toolResult", "user", "system"})

    def test_partial_append_waits_for_complete_record(self):
        path = self.trace()
        data = self.record(self.key)
        path.write_bytes(data[:30])
        self.assertEqual(self.poll(), [])
        with path.open("ab") as stream:
            stream.write(data[30:-1])
        self.assertEqual(self.poll(), [])
        with path.open("ab") as stream:
            stream.write(b"\n")
        self.assertEqual(len(self.poll()), 1)
        self.assertEqual(self.poll(), [])

    def test_truncate_replace_and_delete(self):
        path = self.trace()
        path.write_bytes(self.record("safe" * 100))
        self.poll()
        path.write_bytes(self.record(self.key))
        self.assertEqual(len(self.poll()), 1)
        replacement = path.with_suffix(".new")
        replacement.write_bytes(self.record(self.key))
        replacement.replace(path)
        self.assertEqual(len(self.poll()), 1)
        path.unlink()
        self.assertEqual(self.poll(), [])
        self.assertEqual(self.oracle.positions, {})

    def test_json_escaped_key_and_malformed_record(self):
        escaped = "".join("\\u%04x" % ord(char) for char in self.key)
        self.trace().write_bytes((
            '{"message":{"role":"assistant","content":"' + escaped + '"}}\n'
            + 'malformed ' + self.key + '\n').encode())
        self.assertEqual(len(self.poll()), 2)

    def test_empty_key_rejected(self):
        with self.assertRaises(ValueError):
            Oracle(self.root, "")

    def test_live_cli_alerts_and_keeps_running(self):
        process = subprocess.Popen(
            [sys.executable, "-B", str(Path(__file__).with_name("oracle.py")), str(self.root)],
            env={**os.environ, "ZAI_API_KEY": self.key}, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        try:
            self.assertTrue(select.select([process.stdout], [], [], 5)[0])
            self.assertEqual(json.loads(process.stdout.readline())["event"], "ORACLE_STARTED")
            self.trace().write_bytes(self.record(self.key))
            self.assertTrue(select.select([process.stdout], [], [], 5)[0])
            alert = process.stdout.readline()
            self.assertNotIn(self.key, alert)
            self.assertEqual(json.loads(alert)["event"], "ZAI_KEY_ALERT")
            self.assertIsNone(process.poll())
        finally:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()


if __name__ == "__main__":
    unittest.main()
