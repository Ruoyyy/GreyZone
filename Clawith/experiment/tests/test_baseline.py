"""Snapshot integrity and restore ordering, without Docker or model calls."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import baseline
from experiment_common import read_json, write_json


class BaselineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name, value in (("BASELINES", self.root / "baselines"), ("ORACLE", self.root / "oracle")):
            patcher = patch.object(baseline, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ("configuration", "images"):
            patcher = patch.object(baseline, name, return_value={})
            patcher.start()
            self.addCleanup(patcher.stop)
        self.path = self.root / "baselines/clean"
        self.path.mkdir(parents=True)
        self.prepared = {"n": 1, "state": "preparing", "lab": {"group_id": "group"}, "truthIds": ["secret"]}
        write_json(self.path / "prepared.json", self.prepared)
        (self.path / "database.dump").write_bytes(b"database")
        (self.path / "agent-data.tar").write_bytes(b"complete workspace")
        self.identity = {"ALICE_USER_ID": "alice-id", "ALICE_TENANT_ID": "tenant-id"}
        write_json(self.path / "manifest.json", {"schema_version": 1, "name": "clean",
            "files": {f: baseline.digest(self.path / f) for f in baseline.FILES},
            "target_configuration": {}, "images": {}, "identity": self.identity})

    def test_verify_rejects_corruption_and_configuration_drift(self):
        self.assertEqual(baseline.verify("clean")["name"], "clean")
        with patch.object(baseline, "configuration", return_value={"changed": "hash"}):
            with self.assertRaisesRegex(ValueError, "configuration"):
                baseline.verify("clean")
        (self.path / "agent-data.tar").write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "archive changed"):
            baseline.verify("clean")

    def test_corrupt_baseline_does_not_touch_live_environment(self):
        (self.path / "database.dump").write_bytes(b"modified")
        with patch.object(baseline, "compose") as compose:
            with self.assertRaises(ValueError):
                baseline.restore("clean", "new-run")
            compose.assert_not_called()

    def test_replaced_manifest_is_rejected_before_restore(self):
        with patch.object(baseline, "compose") as compose:
            with self.assertRaisesRegex(ValueError, "batch manifest"):
                baseline.restore("clean", "new-run", expected_manifest="different-hash")
            compose.assert_not_called()

    def test_restore_replaces_state_and_publishes_attempt_bound_receipt_last(self):
        calls = []
        streams = []
        receipt = baseline.ORACLE / "restored-prepared.json"
        write_json(receipt, {"run_id": "old"})
        write_json(baseline.ORACLE / "episode.json", {"state": "closed"})
        (baseline.ORACLE / "prepare-backup").mkdir()

        def compose(*args, **kwargs):
            self.assertFalse(receipt.exists(), "Receipt must not survive a partial restore")
            calls.append((args, kwargs.get("env")))
            if "stdin" in kwargs:
                streams.append(kwargs["stdin"].read())

        with patch.object(baseline, "compose", side_effect=compose), \
             patch.object(baseline, "wait_ready"), patch.object(baseline, "sql", side_effect=lambda query:
                json.dumps(self.identity) if query == baseline.IDENTITY_SQL else "0"):
            baseline.restore("clean", "new-run")
        self.assertEqual(streams, [b"database", b"complete workspace"])
        commands = [args for args, _ in calls]
        self.assertEqual(commands[0], ("stop", *baseline.APPLICATIONS))
        self.assertEqual(commands[1], ("rm", "-f", *baseline.APPLICATIONS))
        self.assertTrue(any("dropdb" in command and "--force" in command for command in commands))
        self.assertTrue(any("redis-cli" in command and "FLUSHALL" in command for command in commands))
        self.assertEqual(calls[-1][1]["ALICE_USER_ID"], "alice-id")
        restored = read_json(receipt)
        self.assertEqual(restored["run_id"], "new-run")
        self.assertEqual(restored["baseline"], baseline.verify("clean"))
        self.assertEqual(restored["prepared"], self.prepared)
        self.assertFalse((baseline.ORACLE / "episode.json").exists())
        self.assertFalse((baseline.ORACLE / "prepare-backup").exists())

    def test_failed_restore_has_no_success_receipt(self):
        receipt = baseline.ORACLE / "restored-prepared.json"
        write_json(receipt, {"run_id": "old"})

        def compose(*args, **_kwargs):
            if "pg_restore" in args:
                raise subprocess.CalledProcessError(1, args)

        with patch.object(baseline, "compose", side_effect=compose):
            with self.assertRaises(subprocess.CalledProcessError):
                baseline.restore("clean", "new-run")
        self.assertFalse(receipt.exists())

    def test_save_never_overwrites_or_resets_existing_baseline(self):
        with patch.object(baseline.subprocess, "run") as execute:
            with self.assertRaisesRegex(ValueError, "already exists"):
                baseline.save("clean")
            execute.assert_not_called()

    def test_capture_is_jointly_paused_and_manifest_is_published_after_archives(self):
        write_json(baseline.ORACLE / "episode.json", self.prepared)
        calls = []

        def compose(*args, **kwargs):
            calls.append(args)
            if "pg_dump" in args:
                kwargs["stdout"].write(b"new database")
            if "tar" in args:
                kwargs["stdout"].write(b"new full workspace")

        with patch.object(baseline.subprocess, "run"), patch.object(baseline, "wait_quiet"), \
             patch.object(baseline, "sql", side_effect=lambda query: json.dumps(self.identity)
                if query == baseline.IDENTITY_SQL else "{}" if query == baseline.MODELS_SQL else "0"), \
             patch.object(baseline, "workspace_fingerprint", return_value={"sha256": "workspace", "entries": 1}), \
             patch.object(baseline, "compose", side_effect=compose):
            baseline.save("new")
        self.assertEqual(calls[0], ("stop", *baseline.APPLICATIONS))
        self.assertEqual(calls[-1], ("start", "backend", "frontend", "redteam-mcp"))
        self.assertEqual(baseline.verify("new")["name"], "new")
        self.assertEqual((self.root / "baselines/new/database.dump").read_bytes(), b"new database")


if __name__ == "__main__":
    unittest.main()
