"""Validate private evidence and write the stable experiment CSV schema."""
import argparse
import csv
import math
import statistics
from datetime import datetime
from pathlib import Path

from experiment_common import SCENARIOS, read_json, write_json

RUN_FIELDS = (
    "scenario,repeat,attempt,status,score,success,points,max_points,reason,path,"
    "started_at,finished_at,duration_seconds,framework_duration_seconds,oracle_wait_seconds,"
    "token_usage_status,token_usage_source,framework_input_tokens,framework_output_tokens,framework_total_tokens"
).split(",")
SUMMARY_FIELDS = (
    "scenario,planned_runs,valid_runs,invalid_attempts,status,mean_score,max_score,min_score,std_score,"
    "success_count,success_rate,mean_duration_seconds,total_duration_seconds,"
    "mean_framework_duration_seconds,total_framework_duration_seconds,mean_framework_tokens,"
    "total_framework_tokens,token_usage_attempts,valid_token_runs,approximate_token_attempts"
).split(",")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0, "Expected a UTC timestamp")
    return parsed


def validate(path, run):
    """Only the selected Oracle surface determines scores; grader failures are allowed."""
    framework = run["framework"]
    allowed_codes = (0, 100) if framework == "promptfoo" else (0,)
    require(run.get("returncode") in allowed_codes, "Framework did not complete successfully")
    require(not run.get("execution_error"), run.get("execution_error"))
    task = read_json(path / "framework/task.json")
    require(task.get("status") == "completed" and task.get("scenario") == run["scenario"]
            and task.get("run_id") == run["run_id"], "Missing or incomplete fixed victim task")
    for evidence in ("oracle/before.json", "oracle/after.json", "framework/victim-reply.json"):
        read_json(path / evidence)
    scenario = read_json(SCENARIOS / run["scenario"] / "scenario.json")
    channels = {attack["channel"] for attack in task["attacks"]}
    require(bool(channels) and channels <= set(scenario["attacker"]["channels"]), "Missing or out-of-scope seller writes")
    result = read_json(path / "oracle/result.json")
    evaluation = result["evaluation"]
    require(evaluation.get("application") == "shopware-claude-commerce"
            and evaluation.get("scenarioId") == run["scenario"]
            and evaluation.get("runId") == run["run_id"], "Oracle identity mismatch")
    require(evaluation.get("status") == "completed" and not evaluation.get("errors"), "Oracle execution did not complete")
    require(utc(run["started_at"]) <= utc(evaluation["startedAt"])
            <= utc(evaluation["closedAt"]) <= utc(run["finished_at"]), "Oracle timestamps are outside this attempt")
    provenance = evaluation["provenance"]
    require(provenance.get("revision") == run["revision"]
            and provenance.get("victimModel") == run["victim_model"]
            and provenance.get("attackerModel") == run["attacker_model"], "Oracle provenance mismatch")
    surfaces = [surface for surface in evaluation["surfaces"] if surface.get("inScope") is True]
    require(len(surfaces) == 1 and [s["id"] for s in surfaces] == scenario["oracleSurfaces"], "Wrong Oracle surface")
    score = surfaces[0]["score"]
    points, maximum, normalized = (score[key] for key in ("points", "maxPoints", "normalized"))
    require(all(number(v) for v in (points, maximum, normalized)) and maximum > 0
            and 0 <= points <= maximum and 0 <= normalized <= 1
            and math.isclose(points / maximum, normalized, abs_tol=1e-8), "Invalid Oracle score")
    success = evaluation["success"]
    require(type(success) is bool and success == surfaces[0]["success"] and success == (normalized > 0), "Inconsistent Oracle success")
    require((path / "oracle/alerts.jsonl").is_file(), "Oracle alerts are missing")
    require(str(task["episode"]) == evaluation["episodeId"], "Oracle episode mismatch")
    if framework == "promptfoo":
        files = list((path / "framework").glob("*/results.json"))
        require(len(files) == 1, "Expected one Promptfoo result")
        report = read_json(files[0])["results"]
        require(report["stats"]["errors"] == 0 and len(report["results"]) == 1, "Promptfoo target error or multiple cases")
        response = report["results"][0]["response"]
        require(not response.get("error"), "Promptfoo provider error")
        native = response["metadata"]
    else:
        files = list((path / "framework").glob("*/summary.json"))
        require(len(files) == 1, "Expected one native framework summary")
        native = read_json(files[0])
        require(native["scenario"] == run["scenario"], "Native scenario mismatch")
        if framework != "dreadnode":
            require(native.get("status") == "completed" and native.get("writeAttempted") is True, "Native attack did not complete")
    require(Path(native["resultPath"]).name == f"result-{task['episode']}.json"
            and Path(native["resultPath"]).parent == (path / "oracle").resolve(), "Native result belongs to another attempt")
    return {"score": normalized, "success": success, "points": points, "max_points": maximum}


def classify(path, run):
    for key in ("score", "success", "points", "max_points"):
        run.pop(key, None)
    try:
        run.update(validate(path, run), status="valid", reason="")
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
        run.update(status="invalid", reason=str(error))
    write_json(path / "run.json", run)
    return run


def load_runs(batch):
    rows = []
    for path in sorted(batch.glob("*/repeat-*/attempt-*/run.json")):
        run = classify(path.parent, read_json(path))
        run["path"] = str(path.parent.relative_to(batch))
        rows.append(run)
    return rows


def aggregate(rows, manifest):
    summaries = []
    for scenario in manifest["scenarios"]:
        attempts = [r for r in rows if r["scenario"] == scenario]
        # One valid result per planned repeat, even if an extra attempt was copied in.
        valid = {r["repeat"]: r for r in attempts if r["status"] == "valid"}
        require(all(1 <= n <= manifest["repeats"] for n in valid), "Repeat outside the manifest plan")
        require(len(valid) == sum(r["status"] == "valid" for r in attempts), "Duplicate valid repeat")
        valid = list(valid.values())
        scores = [r["score"] for r in valid]
        summary = {"scenario": scenario, "planned_runs": manifest["repeats"], "valid_runs": len(valid),
                   "invalid_attempts": sum(r["status"] != "valid" for r in attempts),
                   "status": "complete" if len(valid) == manifest["repeats"] else "incomplete",
                   "mean_score": statistics.mean(scores) if scores else None,
                   "max_score": max(scores) if scores else None, "min_score": min(scores) if scores else None,
                   "std_score": statistics.stdev(scores) if len(scores) > 1 else None,
                   "success_count": sum(r["success"] for r in valid),
                   "success_rate": statistics.mean(r["success"] for r in valid) if valid else None}
        for field, mean_key, total_key in (
            ("duration_seconds", "mean_duration_seconds", "total_duration_seconds"),
            ("framework_duration_seconds", "mean_framework_duration_seconds", "total_framework_duration_seconds"),
            ("framework_total_tokens", "mean_framework_tokens", "total_framework_tokens"),
        ):
            measured = [r[field] for r in valid if number(r.get(field))]
            all_measured = [r[field] for r in attempts if number(r.get(field))]
            summary[mean_key] = statistics.mean(measured) if measured else None
            summary[total_key] = sum(all_measured) if all_measured else None
        summary.update(token_usage_attempts=sum(number(r.get("framework_total_tokens")) for r in attempts),
                       valid_token_runs=sum(number(r.get("framework_total_tokens")) for r in valid),
                       approximate_token_attempts=sum(r.get("token_usage_status") == "approximate" for r in attempts))
        summaries.append(summary)
    return summaries


def write_csv(path, fields, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: str(v).lower() if type(v) is bool else round(v, 6) if type(v) is float else v
                             for k, v in row.items()})
    path.chmod(0o600)


def collect(batch):
    manifest = read_json(batch / "manifest.json")
    rows = load_runs(batch)
    summaries = aggregate(rows, manifest)
    write_csv(batch / "runs.csv", RUN_FIELDS, rows)
    write_csv(batch / "summary.csv", SUMMARY_FIELDS, summaries)
    return all(s["status"] == "complete" for s in summaries)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", type=Path)
    args = parser.parse_args()
    collect(args.batch.resolve())
    print(f"Updated {args.batch}/runs.csv and summary.csv (no services or models called)")
