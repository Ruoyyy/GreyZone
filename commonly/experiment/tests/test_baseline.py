import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from support import BASELINE, RUN_ID
import baseline as B
from environment import VOLUMES, SERVICES
from experiment_common import digest, read_json, write_json


class BaselineTests(unittest.TestCase):
    def baseline(self, root):
        path = root / "clean"
        path.mkdir()
        for logical in VOLUMES:
            (path / f"{logical}.tar").write_bytes(b"synthetic archive")
        write_json(path / "manifest.json", {"schema_version": 1, "name": "clean", "images": {"secrets": "image-id"},
                   "target_configuration": {"source": "hash"}, "files": {p.name: digest(p) for p in path.glob("*.tar")}})
        return path

    def test_corrupt_or_incomplete_archive_does_not_touch_live_services(self):
        for corrupt in (True, False):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                path = self.baseline(root)
                if corrupt:
                    (path / "mongo-data.tar").write_bytes(b"corrupt")
                else:
                    (path / "postgres-data.tar").unlink()
                with patch.object(B, "BASELINES", root), patch.object(B, "compose") as compose:
                    with self.assertRaises((ValueError, OSError)):
                        B.restore("clean", RUN_ID, 99)
                    compose.assert_not_called()

    def test_restore_validates_all_archives_before_stopping_and_publishes_receipt_last(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.baseline(root)
            receipt = root / "oracle/restored-baseline.json"
            calls = []
            def command(*args, **kwargs):
                calls.append(args)
                self.assertFalse(receipt.exists())
            mapping = {v: "commonly_" + v for v in VOLUMES}
            with patch.object(B, "BASELINES", root), patch.object(B, "RECEIPT", receipt), \
                 patch.object(B, "configuration", return_value={"source": "hash"}), \
                 patch.object(B, "images", return_value={"secrets": "image-id"}), \
                 patch.object(B, "volumes", return_value=mapping), patch.object(B, "check_volume_ownership"), \
                 patch.object(B, "compose", side_effect=command), patch.object(B.subprocess, "run", side_effect=command), \
                 patch.object(B, "archive_episode"), patch.object(B, "wait_quiet"), patch.object(B, "verify_substrate"):
                B.restore("clean", RUN_ID, 99)
            self.assertEqual(read_json(receipt)["runId"], RUN_ID)
            helpers = [c[0] for c in calls if isinstance(c[0], list)]
            self.assertEqual(len(helpers), 7)
            self.assertTrue(all("--network" in c and "none" in c and "--read-only" in c for c in helpers))
            self.assertTrue(any(c[0] == "rm" and c[1] == "-f" for c in calls))
            self.assertFalse(any("seed" in c for c in calls))
            self.assertEqual(calls[-1][-len(SERVICES):], SERVICES)

    def test_replaced_manifest_fails_before_any_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.baseline(root)
            with patch.object(B, "BASELINES", root), patch.object(B, "configuration", return_value={"source": "hash"}), \
                 patch.object(B, "images", return_value={"secrets": "image-id"}), patch.object(B, "compose") as compose:
                with self.assertRaisesRegex(ValueError, "differs"):
                    B.restore("clean", RUN_ID, 99, "wrong-hash")
                compose.assert_not_called()

    def test_failed_restore_has_no_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.baseline(root)
            receipt = root / "receipt.json"
            with patch.object(B, "BASELINES", root), patch.object(B, "RECEIPT", receipt), \
                 patch.object(B, "verify", return_value=BASELINE), patch.object(B, "archive_episode"), \
                 patch.object(B, "volumes", return_value={v: "commonly_" + v for v in VOLUMES}), \
                 patch.object(B, "check_volume_ownership"), patch.object(B, "compose"), \
                 patch.object(B.subprocess, "run", side_effect=RuntimeError("simulated extraction failure")):
                with self.assertRaises(RuntimeError):
                    B.restore("clean", RUN_ID, 99)
                self.assertFalse(receipt.exists())

    def test_capture_never_overwrites_existing_baseline(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "clean").mkdir()
            with patch.object(B, "BASELINES", root), patch.object(B.subprocess, "run") as execute:
                with self.assertRaises(ValueError):
                    B.save("clean", 99)
                execute.assert_not_called()

    def test_capture_stops_databases_and_archives_seven_volumes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            order = []
            def compose(*args, **kwargs):
                order.append(args)
            def execute(command, **kwargs):
                if "stdout" in kwargs:
                    kwargs["stdout"].write(b"archive")
                    self.assertIn(("stop", "--timeout", "60", "litellm", "postgres", "mongo"), order)
            with patch.object(B, "BASELINES", root), patch.object(B.subprocess, "run", side_effect=execute), \
                 patch.object(B, "wait_quiet"), patch.object(B, "verify_substrate"), patch.object(B, "archive_episode"), \
                 patch.object(B, "quiet", return_value=True), patch.object(B, "compose", side_effect=compose), \
                 patch.object(B, "check_volume_ownership"), patch.object(B, "configuration", return_value={}), \
                 patch.object(B, "images", return_value={"secrets": "helper"}), \
                 patch.object(B, "volumes", return_value={v: "commonly_" + v for v in VOLUMES}):
                B.save("new", 99)
            self.assertEqual(len(read_json(root / "new/manifest.json")["files"]), 7)
            self.assertTrue(all((p.stat().st_mode & 0o777) == 0o600 for p in (root / "new").iterdir()))

    def test_interrupted_volume_transfer_removes_its_docker_helper(self):
        with patch.object(B.subprocess, "run", side_effect=[KeyboardInterrupt(), Mock(returncode=0)]) as execute:
            with self.assertRaises(KeyboardInterrupt):
                B.transfer("commonly_mongo-data", "image-id", Mock())
            command = execute.call_args_list[0].args[0]
            name = command[command.index("--name") + 1]
            self.assertEqual(execute.call_args_list[1].args[0], ["docker", "rm", "-f", name])
