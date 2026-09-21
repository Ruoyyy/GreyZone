#!/usr/bin/env python3
"""离线命令行记账本：新增支出、查看记录、分类汇总。"""

import argparse
from datetime import date
from decimal import Decimal, localcontext
import json
from pathlib import Path
import re
import tempfile


def amount_cents(value):
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", value):
        raise argparse.ArgumentTypeError("金额须为正数，最多两位小数")
    with localcontext() as context:
        context.prec = max(28, len(value) + 2)
        cents = int(Decimal(value) * 100)
    if cents <= 0:
        raise argparse.ArgumentTypeError("金额必须大于零")
    return cents


def expense_date(value):
    try:
        if date.fromisoformat(value).isoformat() == value:
            return value
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("日期须为有效的 YYYY-MM-DD")


def category_name(value):
    if not value.strip():
        raise argparse.ArgumentTypeError("分类不能为空")
    return value.strip()


def load_entries(path):
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if not isinstance(entries, list):
        raise ValueError("账本必须是 JSON 数组")
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
            raise ValueError(f"账本第 {number} 条记录格式无效") from None
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
    parser.add_argument("--file", type=Path, default=Path("expenses.json"), help="账本路径")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="新增一笔支出")
    add.add_argument("amount", type=amount_cents, help="金额，最多两位小数")
    add.add_argument("--category", required=True, type=category_name, help="分类")
    add.add_argument("--date", default=date.today().isoformat(), type=expense_date)
    add.add_argument("--note", default="", help="备注")
    commands.add_parser("list", help="按日期升序查看记录，同日保持录入顺序")
    commands.add_parser("summary", help="按分类汇总，并显示总支出")
    args = parser.parse_args()

    try:
        entries = load_entries(args.file)
        if args.command == "add":
            entries.append({"date": args.date, "amount_cents": args.amount,
                            "category": args.category, "note": args.note})
            save_entries(args.file, entries)
            print(f"已记录：{args.date}\t{args.category}\t{money(args.amount)}")
        elif not entries:
            print("暂无记录")
        elif args.command == "list":
            print("日期\t分类\t金额\t备注")
            for entry in sorted(entries, key=lambda item: item["date"]):
                print(f"{entry['date']}\t{entry['category']}\t"
                      f"{money(entry['amount_cents'])}\t{entry['note']}")
        else:
            totals = {}
            for entry in entries:
                category = entry["category"]
                totals[category] = totals.get(category, 0) + entry["amount_cents"]
            print("分类\t金额")
            for category, cents in sorted(totals.items()):
                print(f"{category}\t{money(cents)}")
            print(f"总计\t{money(sum(totals.values()))}")
    except (OSError, ValueError) as error:
        parser.exit(1, f"错误：{error}\n")


if __name__ == "__main__":
    main()
