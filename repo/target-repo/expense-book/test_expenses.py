from datetime import date
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from expenses import save_entries


SCRIPT = Path(__file__).with_name("expenses.py").resolve()


class ExpenseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ledger = self.root / "expenses.json"

    def run_cli(self, *args, success=True):
        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), *args], cwd=self.root,
            capture_output=True, text=True, timeout=5,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("Traceback", result.stderr)
        return result

    def test_add_persist_and_reload(self):
        self.run_cli("add", "28.50", "--category", "餐饮", "--note", "午饭",
                     "--date", "2026-09-11")
        self.assertEqual(json.loads(self.ledger.read_text()), [{
            "date": "2026-09-11", "amount_cents": 2850, "category": "餐饮", "note": "午饭",
        }])
        self.assertIn("2026-09-11\t餐饮\t28.50\t午饭", self.run_cli("list").stdout)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_default_date_and_empty_note(self):
        self.run_cli("add", "1", "--category", " food ")
        row = json.loads(self.ledger.read_text())[0]
        self.assertEqual(row["date"], date.today().isoformat())
        self.assertEqual(row["category"], "food")
        self.assertEqual(row["note"], "")

    def test_exact_category_totals(self):
        for amount, category in (("0.10", "food"), ("0.20", "food"), ("2.05", "bus")):
            self.run_cli("add", amount, "--category", category)
        self.assertEqual(self.run_cli("summary").stdout,
                         "分类\t金额\nbus\t2.05\nfood\t0.30\n总计\t2.35\n")

    def test_list_order_is_stable_and_does_not_rewrite(self):
        for day, note in (("2026-09-11", "later"), ("2026-09-10", "first"),
                          ("2026-09-10", "second")):
            self.run_cli("add", "1", "--category", "food", "--date", day, "--note", note)
        original = self.ledger.read_bytes()
        rows = self.run_cli("list").stdout.splitlines()[1:]
        self.assertEqual([row.split("\t")[-1] for row in rows], ["first", "second", "later"])
        self.run_cli("summary")
        self.assertEqual(self.ledger.read_bytes(), original)

    def test_empty_book_does_not_create_file(self):
        for command in ("list", "summary"):
            self.assertEqual(self.run_cli(command).stdout.strip(), "暂无记录")
        self.assertFalse(self.ledger.exists())

    def test_custom_file(self):
        self.run_cli("--file", "travel.json", "add", "5", "--category", "bus")
        self.assertFalse(self.ledger.exists())
        self.assertIn("总计\t5.00", self.run_cli("--file", "travel.json", "summary").stdout)

    def test_invalid_amount_does_not_modify_book(self):
        self.ledger.write_text("[]\n")
        for amount in ("0", "-1", "1.001", "NaN", "Infinity", "1e2", "abc", "", "1.230"):
            with self.subTest(amount=amount):
                self.run_cli("add", amount, "--category", "food", success=False)
                self.assertEqual(self.ledger.read_text(), "[]\n")

    def test_invalid_date_and_category_do_not_create_file(self):
        for day in ("2026-02-29", "2026-13-01", "20260911", "2026-W37-5", "tomorrow"):
            with self.subTest(day=day):
                self.run_cli("add", "1", "--category", "food", "--date", day, success=False)
        self.run_cli("add", "1", "--category", "   ", success=False)
        self.assertFalse(self.ledger.exists())

    def test_corrupt_book_is_never_overwritten(self):
        for content in (b"{broken", b"{}", b"[null]", b"\xff"):
            with self.subTest(content=content):
                self.ledger.write_bytes(content)
                for args in (("list",), ("summary",), ("add", "1", "--category", "food")):
                    self.run_cli(*args, success=False)
                    self.assertEqual(self.ledger.read_bytes(), content)

    def test_invalid_stored_record_is_rejected(self):
        valid = {"date": "2026-09-11", "amount_cents": 100, "category": "food", "note": ""}
        for field, value in (("amount_cents", True), ("amount_cents", 1.5),
                             ("amount_cents", -10), ("date", "invalid"),
                             ("date", None), ("category", ""), ("note", None)):
            with self.subTest(field=field, value=value):
                content = json.dumps([{**valid, field: value}])
                self.ledger.write_text(content)
                self.run_cli("add", "1", "--category", "food", success=False)
                self.assertEqual(self.ledger.read_text(), content)

    def test_failed_replace_preserves_book_and_cleans_temporary(self):
        self.ledger.write_text("[]\n")
        with patch.object(Path, "replace", side_effect=OSError("simulated write failure")):
            with self.assertRaises(OSError):
                save_entries(self.ledger, [{"test": "new data"}])
        self.assertEqual(self.ledger.read_text(), "[]\n")
        self.assertEqual(list(self.root.iterdir()), [self.ledger])

    def test_example_book(self):
        example = SCRIPT.parent / "examples" / "expenses.json"
        self.assertIn("总计\t48.50", self.run_cli("--file", str(example), "summary").stdout)


if __name__ == "__main__":
    unittest.main()
