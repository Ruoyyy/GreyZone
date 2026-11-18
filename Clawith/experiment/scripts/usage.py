"""Read native usage and response journals; missing usage is never zero."""
import json
import math
import re
from pathlib import Path

from experiment_common import read_json, write_json


def count(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def combine(records, source):
    if not records:
        return {"status": "unavailable", "source": source}
    result = {"source": source, "calls": len(records)}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        known = [r[key] for r in records if count(r.get(key))]
        result[key] = sum(known) if known else None
    result["measured_calls"] = sum(count(r.get("total_tokens")) for r in records)
    result["status"] = "reported" if all(all(count(r.get(k)) for k in
        ("input_tokens", "output_tokens", "total_tokens")) for r in records) else "partial"
    return result


def native_usage(path, framework):
    directory = path / "framework"
    if framework in ("deepteam", "pyrit"):
        file = directory / "usage.jsonl"
        rows = []
        if file.exists():
            for index, line in enumerate(file.read_text().splitlines()):
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    rows.append({"kind": "request_start", "request_id": f"unparsed-{index}"})
        if any(row.get("kind") == "usage_start" for row in rows):
            requests = {row["request_id"]: {} for row in rows if row.get("kind") == "request_start"}
            requests.update({row["request_id"]: row for row in rows if row.get("kind") == "response"})
            if not requests:
                return {"status": "reported", "source": framework + ".openai.response.usage", "calls": 0,
                        "measured_calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            return combine(list(requests.values()), framework + ".openai.response.usage")
        # SDK response IDs are stable, even if a wrapper sees the same response twice.
        rows = list({row.get("id", str(i)): row for i, row in enumerate(rows)}.values())
        return combine(rows, framework + ".openai.response.usage")
    if framework == "promptfoo":
        rows = []
        generation = False
        for file in directory.glob("*/promptfoo-results.json"):
            usage = read_json(file).get("results", {}).get("stats", {}).get("tokenUsage", {})
            for bucket in ("attacker", "assertions", "generation"):
                if bucket in usage:
                    item = usage[bucket]
                    rows.append({"input_tokens": item.get("prompt"), "output_tokens": item.get("completion"),
                                 "total_tokens": item.get("total")})
                    generation |= bucket == "generation"
        text = (path / "console.log").read_text(errors="replace") if (path / "console.log").exists() else ""
        if not generation:
            matches = re.findall(r"Observed generation token usage:\s*([\d,]+) total \(([\d,]+) input, ([\d,]+) output\)", text)
            for total, inputs, outputs in matches:
                rows.append(dict(zip(("total_tokens", "input_tokens", "output_tokens"),
                                     (int(v.replace(",", "")) for v in (total, inputs, outputs)))))
            generation = bool(matches)
        result = combine(rows, "promptfoo.native.attacker+assertions+generation")
        if rows and not generation:
            result["status"] = "partial"
        return result
    logs = list(directory.glob("*/dreadnode.log"))
    if not logs and (path / "console.log").exists():
        logs = [path / "console.log"]
    matches = []
    for file in logs:
        text = re.sub(r"\x1b\[[0-9;]*m", "", file.read_text(errors="replace"))
        matches += re.findall(r"(?:^|[\s·])([\d,.]+)([kKmM]?) tokens\b", text)
    if matches:
        value, suffix = matches[-1]
        return {"status": "approximate" if suffix else "reported", "source": "dreadnode.native.console",
                "total_tokens": round(float(value.replace(",", "")) * {"": 1, "k": 1000, "m": 1000000}[suffix.lower()]),
                "input_tokens": None, "output_tokens": None}
    return {"status": "unavailable", "source": framework}


def update_usage(path, run):
    try:
        result = native_usage(path, run["framework"])
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as error:
        result = {"status": "unavailable", "source": run["framework"], "error": str(error)}
    if result["status"] == "reported" and (run.get("execution_error") or run.get("framework_status") == "error"):
        result["status"] = "partial"
    run.update(token_usage_status=result["status"], token_usage_source=result["source"])
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        run["framework_" + key] = result.get(key)
    run["usage_coverage"] = {key: result[key] for key in ("calls", "measured_calls", "error") if key in result}
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Backfill native usage without attacks or Docker")
    parser.add_argument("batch", type=Path, nargs="?")
    parser.add_argument("--series", type=Path)
    args = parser.parse_args()
    if bool(args.batch) == bool(args.series):
        parser.error("Provide one batch path or --series")
    series = read_json(args.series) if args.series else None
    batches = [args.series.parent / fw / name for fw, name in series["batches"].items()] if series else [args.batch]
    from summarize import collect
    for batch in batches:
        if not (batch / "manifest.json").exists():
            continue
        for file in batch.glob("*/repeat-*/attempt-*/run.json"):
            run = read_json(file)
            update_usage(file.parent, run)
            write_json(file, run)
        collect(batch)
