#!/usr/bin/env python3
"""Offline command-line expense book: add expenses, view records, summarize by category."""

import argparse
from datetime import date
from decimal import Decimal, localcontext
import json
from pathlib import Path
import re
import tempfile


def amount_cents(value):
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", value):
        raise argparse.ArgumentTypeError("amount must be a positive number with at most two decimal places")
    with localcontext() as context:
        context.prec = max(28, len(value) + 2)
        cents = int(Decimal(value) * 100)
    if cents <= 0:
        raise argparse.ArgumentTypeError("amount must be greater than zero")
    return cents


def expense_date(value):
    try:
        if date.fromisoformat(value).isoformat() == value:
            return value
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("date must be a valid YYYY-MM-DD")


def category_name(value):
    if not value.strip():
        raise argparse.ArgumentTypeError("category must not be empty")
    return value.strip()


def load_entries(path):
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if not isinstance(entries, list):
        raise ValueError("the ledger must be a JSON array")
    for number, entry in enumerate(entries, 1):
        try:
            if (
                not isinstance(entry, dict)
                or set(entry) != {"date", "amount_cents", "category", "note"}
                or type(entry["amount_cents"]) is not int
                or entry["amount_cents"] <= 0
                or not isinstance(entry["category"], str)
                or not entry["category"].strip()
                or not isinstance(entry["note"], str)
                or date.fromisoformat(entry["date"]).isoformat() != entry["date"]
            ):
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError(f"ledger record {number} has an invalid format") from None
    return entries


def save_entries(path, entries):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(entries, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def money(cents):
    return f"{cents // 100}.{cents % 100:02d}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=Path("expenses.json"), help="ledger path")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="add an expense")
    add.add_argument("amount", type=amount_cents, help="amount, at most two decimal places")
    add.add_argument("--category", required=True, type=category_name, help="category")
    add.add_argument("--date", default=date.today().isoformat(), type=expense_date)
    add.add_argument("--note", default="", help="note")
    commands.add_parser("list", help="list records by date ascending; keep entry order within a day")
    commands.add_parser("summary", help="summarize by category and show total spending")
    args = parser.parse_args()

    try:
        entries = load_entries(args.file)
        if args.command == "add":
            entries.append({"date": args.date, "amount_cents": args.amount,
                            "category": args.category, "note": args.note})
            save_entries(args.file, entries)
            print(f"Recorded: {args.date}\t{args.category}\t{money(args.amount)}")
        elif not entries:
            print("No entries")
        elif args.command == "list":
            print("Date\tCategory\tAmount\tNote")
            for entry in sorted(entries, key=lambda item: item["date"]):
                print(f"{entry['date']}\t{entry['category']}\t"
                      f"{money(entry['amount_cents'])}\t{entry['note']}")
        else:
            totals = {}
            for entry in entries:
                category = entry["category"]
                totals[category] = totals.get(category, 0) + entry["amount_cents"]
            print("Category\tAmount")
            for category, cents in sorted(totals.items()):
                print(f"{category}\t{money(cents)}")
            print(f"Total\t{money(sum(totals.values()))}")
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
