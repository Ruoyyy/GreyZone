#!/usr/bin/env python3
"""Build per-framework CSV statistics from archived Oracle results only."""
import argparse
import csv
import math
import statistics

from common import RESULTS, SCENARIOS, FRAMEWORKS, framework_completed, read_json, safe_name
from usage import count as is_number, framework_usage


def oracle_score(result, scenario, episode=None, run_id=None):
    evaluation = result["evaluation"]
    if evaluation["application"] != "Letta_Issue_Agent":
        raise ValueError("Unexpected application")
    if evaluation["scenarioId"] != scenario or evaluation["status"] != "completed":
        raise ValueError("Oracle scenario mismatch or incomplete evaluation")
    if episode is not None and str(evaluation["episodeId"]) != str(episode):
        raise ValueError("Oracle episode mismatch")
    if run_id is not None and evaluation.get("runId") != run_id:
        raise ValueError("Oracle run ID mismatch")
    if result.get("executionError") or type(evaluation.get("success")) is not bool:
        raise ValueError("Oracle execution error or missing success flag")
    surface_id, = read_json(SCENARIOS / scenario / "scenario.json")["oracleMonitors"]
    surface, = [s for s in evaluation["surfaces"] if s["inScope"]]
    if surface["id"] != surface_id:
        raise ValueError("Unexpected scoring surface")
    score = surface["score"]
    points, maximum, normalized = (score[k] for k in ("points", "maxPoints", "normalized"))
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (points, maximum, normalized)):
        raise ValueError("Non-numeric Oracle score")
    if maximum <= 0 or not 0 <= points <= maximum or not 0 <= normalized <= 1:
        raise ValueError("Oracle score out of bounds")
    if abs(normalized - points / maximum) > 0.000001:
        raise ValueError("Inconsistent Oracle score")
    return normalized, evaluation["success"], points, maximum


def write_csv(path, fields, rows):
    temporary = path.with_suffix(".csv.tmp")
    with temporary.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def summarize(batch):
    manifest = read_json(batch / "manifest.json")
    rows, summaries = [], []
    for scenario in manifest["scenarios"]:
        scores, successes, valid_repeats = [], 0, set()
        invalid = 0
        scenario_rows = []
        for path in sorted((batch / scenario).glob("repeat-*/attempt-*/run.json")):
            run = read_json(path)
            usage = run.get("token_usage") or framework_usage(path.parent, manifest.get("framework", ""))
            row = {"scenario": scenario, "repeat": run["repeat"], "attempt": run["attempt"],
                   "issue": run.get("issue", ""), "status": "invalid", "score": "",
                   "success": "", "points": "", "max_points": "", "reason": "",
                   "path": str(path.parent.relative_to(batch))}
            row.update(started_at=run.get("started_at", ""), finished_at=run.get("finished_at", ""),
                       duration_seconds=run.get("duration_seconds", ""),
                       framework_duration_seconds=run.get("framework_duration_seconds", ""),
                       token_usage_status=usage["status"], token_usage_source=usage["source"])
            for field in ("input_tokens", "output_tokens", "total_tokens"):
                value = usage.get(field)
                row["framework_" + field] = value if is_number(value) else ""
            try:
                if run.get("status") != "completed" or not framework_completed(path.parent, run):
                    raise ValueError(run.get("error") or "Execution or collection incomplete")
                score, success, points, maximum = oracle_score(
                    read_json(path.parent / "oracle/result.json"), scenario,
                    run["issue"], run["run_id"])
                if run["repeat"] in valid_repeats:
                    raise ValueError("Duplicate valid repetition")
                valid_repeats.add(run["repeat"])
                scores.append(score)
                successes += success
                row.update(status="completed", score=score, success=success,
                           points=points, max_points=maximum)
            except (KeyError, ValueError, TypeError, OSError) as exc:
                invalid += 1
                row["reason"] = str(exc)
            rows.append(row)
            scenario_rows.append(row)
        count = len(scores)
        summaries.append({"scenario": scenario, "planned_runs": manifest["repeats"],
            "valid_runs": count, "invalid_attempts": invalid,
            "status": "completed" if count == manifest["repeats"] else "incomplete",
            "mean_score": statistics.mean(scores) if count else "",
            "max_score": max(scores) if count else "", "min_score": min(scores) if count else "",
            "std_score": statistics.stdev(scores) if count > 1 else "",
            "success_count": successes, "success_rate": successes / count if count else ""})
        summary = summaries[-1]
        valid_rows = [r for r in scenario_rows if r["status"] == "completed"]
        for field in ("duration_seconds", "framework_duration_seconds", "framework_total_tokens"):
            known = [r[field] for r in scenario_rows if is_number(r[field])]
            valid = [r[field] for r in valid_rows if is_number(r[field])]
            label = "framework_tokens" if field == "framework_total_tokens" else field
            summary["mean_" + label] = statistics.mean(valid) if valid else ""
            summary["total_" + label] = sum(known) if known else ""
        summary["token_usage_attempts"] = sum(is_number(r["framework_total_tokens"]) for r in scenario_rows)
        summary["valid_token_runs"] = sum(is_number(r["framework_total_tokens"]) for r in valid_rows)
        summary["approximate_token_attempts"] = sum(r["token_usage_status"] == "approximate" for r in scenario_rows)
    write_csv(batch / "runs.csv", ["scenario", "repeat", "attempt", "issue", "status", "score",
              "success", "points", "max_points", "reason", "path", "started_at", "finished_at",
              "duration_seconds", "framework_duration_seconds", "token_usage_status", "token_usage_source",
              "framework_input_tokens", "framework_output_tokens", "framework_total_tokens"], rows)
    write_csv(batch / "summary.csv", list(summaries[0]), summaries)
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--framework", choices=FRAMEWORKS, required=True)
    parser.add_argument("--batch", type=safe_name, required=True)
    args = parser.parse_args()
    path = RESULTS / args.framework / args.batch
    summarize(path)
    print(path / "summary.csv")


if __name__ == "__main__":
    main()
