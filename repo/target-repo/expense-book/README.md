# Expense Book (CLI)

An ordinary offline personal expense tracker: add entries, list them, and
summarize by category. Requires Python 3.9 or newer, with no third-party
dependencies. All amounts use a single currency.

## Usage

From the repository root:

```bash
cd expense-book
python3 expenses.py add 28.50 --category food --note lunch
python3 expenses.py add 20 --category transport --date 2026-09-10 --note taxi
python3 expenses.py list
python3 expenses.py summary
```

The ledger is saved as `expenses.json` in the current directory by default.
The date defaults to today; the category is required and the note optional.
Amounts must be positive with at most two decimal places; scientific notation
is not accepted. Dates use `YYYY-MM-DD`. The list is ordered by date ascending
(insertion order within a day); the summary is sorted by category name and
shows the total.

`--file` goes before the subcommand to select a ledger, or to view the sample
without real data:

```bash
python3 expenses.py --help
python3 expenses.py --file examples/expenses.json list
python3 expenses.py --file examples/expenses.json summary
python3 expenses.py --file travel.json add 5 --category bus
```

## Data

Each record in the JSON array contains `date`, `amount_cents`, `category`,
`note`. Amounts are stored as integer cents to avoid `0.1 + 0.2`
floating-point errors.

No need to create a new ledger beforehand; viewing a nonexistent ledger just
reports "no records" and creates no file. Invalid amounts or dates are not
written; a corrupted ledger or invalid fields raise a clear error and never
overwrite the original file. Saving writes a temporary file in the same
directory first, then atomically replaces the ledger; the parent directory
must already exist and be writable.

This is a single-user, single-process tool: concurrent writes to the same
ledger are not supported, and there is no encryption or automatic backup.
The default ledger is ignored by this directory's `.gitignore`; avoid
committing custom ledgers. Month filtering, CSV export, and editing or
deleting records are out of scope for the first version.

## Tests

From the repository root:

```bash
python3 -B -m unittest discover -s expense-book -p 'test_*.py' -v
```

Tests use temporary directories and never touch personal ledgers or sample
files.
