"""Offline checks for the narrowly scoped Dreadnode MCP compatibility fix."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fix_mcp_probe

SOURCE = '''def probe(response):
    if response is not None:
        content_type = response.headers.get("content-type", "").lower()
        if "application/json" not in content_type:
            return False
    return True
'''


class ProbeFixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "client.py"
        self.path.write_text(SOURCE)
        module = SimpleNamespace(client=SimpleNamespace(__file__=str(self.path)))
        self.modules = patch.dict("sys.modules", {"dreadnode.agents.mcp": module})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def apply(self, version="2.0.46"):
        with patch.object(fix_mcp_probe, "version", return_value=version), contextlib.redirect_stdout(io.StringIO()):
            fix_mcp_probe.main()

    def test_only_exact_sdk_rejection_is_accepted(self):
        self.apply()
        namespace = {}
        exec(compile(self.path.read_text(), str(self.path), "exec"), namespace)
        probe = namespace["probe"]
        expected = 'malformed payload: invalid message version tag ""; expected "2.0"'
        self.assertTrue(probe(SimpleNamespace(headers={"content-type": "text/plain"}, status_code=400, text=expected)))
        for status, text in ((401, expected), (400, "Unauthorized"), (500, expected)):
            self.assertFalse(probe(SimpleNamespace(headers={"content-type": "text/plain"}, status_code=status, text=text)))
        self.assertTrue(probe(SimpleNamespace(headers={"content-type": "application/json"}, status_code=200)))

    def test_repeated_application_does_not_change_the_file(self):
        self.apply()
        updated = self.path.read_text()
        self.apply()
        self.assertEqual(self.path.read_text(), updated)

    def test_unknown_version_is_rejected_without_writing(self):
        with self.assertRaises(SystemExit):
            self.apply("2.1.0")
        self.assertEqual(self.path.read_text(), SOURCE)

    def test_unexpected_source_is_rejected_without_writing(self):
        self.path.write_text("# changed SDK\n")
        with self.assertRaises(SystemExit):
            self.apply()
        self.assertEqual(self.path.read_text(), "# changed SDK\n")


if __name__ == "__main__":
    unittest.main()
