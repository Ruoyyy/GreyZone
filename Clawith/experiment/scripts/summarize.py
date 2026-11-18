"""Validate private evidence and write the stable experiment CSV schema."""
import argparse
import csv
import math
import json
import statistics
import hashlib
from datetime import datetime
from pathlib import Path

from experiment_common import SCENARIOS, read_json, write_json
from diagnostics import read_events, target_errors
from usage import update_usage

RUN_FIELDS = (
    "scenario,repeat,attempt,status,score,success,points,max_points,reason,path,"
    "failure_kind,collection_status,diagnostics_status,attack_turns,target_error_count,target_error_codes,started_at,finished_at,duration_seconds,reset_duration_seconds,framework_duration_seconds,oracle_wait_seconds,"
    "token_usage_status,token_usage_source,framework_input_tokens,framework_output_tokens,framework_total_tokens"
).split(",")
SUMMARY_FIELDS = (
    "scenario,planned_runs,valid_runs,invalid_attempts,target_error_attempts,target_error_count,status,mean_score,max_score,min_score,std_score,"
    "success_count,success_rate,success_rate_ci_low,success_rate_ci_high,invalid_attempt_rate,mean_duration_seconds,total_duration_seconds,"
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
    errors = target_errors(read_events(path))
    require(not errors, "Target execution failed: " + "; ".join(error["code"] for error in errors))
    require(not run.get("execution_error"), run.get("execution_error"))
    require(not run.get("collection_error"), run.get("collection_error"))
    allowed = (0, 100) if run["framework"] == "promptfoo" else (0,)
    result = read_json(path / "oracle/result.json")
    ep = read_json(path / "oracle/episode.json")
    evaluation = result["evaluation"]
    require(ep.get("state") == "closed", "Oracle episode is not closed")
    if run.get("baseline"):
        require(ep.get("baseline") == run["baseline"], "Oracle baseline mismatch")
    require(evaluation.get("application") == "Clawith"
            and evaluation.get("scenarioId") == run["scenario"]
            and evaluation.get("runId") == run["run_id"], "Oracle identity mismatch")
    require(str(ep["n"]) == evaluation["episodeId"] == str(result["episode"]), "Episode mismatch")
    require(evaluation.get("status") == "completed" and not evaluation.get("errors"), "Oracle did not complete")
    require(run.get("framework_returncode", run.get("returncode")) in allowed, "Framework execution failed")
    if run.get("schema_version", 1) >= 2:
        require(ep.get("runId") == run["run_id"], "Live episode run identity mismatch")
        require(result.get("settlement", {}).get("status") == "settled", "Target settlement evidence is missing")
        require(evaluation["provenance"].get("attackerModel") == run["attacker_model"]
                and evaluation["provenance"].get("victimModel") == run["victim_model"], "Oracle model provenance mismatch")
    require(utc(run["started_at"]) <= utc(evaluation["startedAt"])
            <= utc(evaluation["closedAt"]) <= utc(run["finished_at"]), "Oracle timestamps outside attempt")
    require(evaluation["provenance"].get("revision") == run["revision"], "Revision mismatch")
    spec = read_json(SCENARIOS / run["scenario"] / "scenario.json")
    surfaces = [s for s in evaluation["surfaces"] if s.get("inScope") is True]
    require([s["id"] for s in surfaces] == spec["oracleSuccessSurfaces"] == ep["surfaces"], "Wrong Oracle surface")
    score = surfaces[0]["score"]
    points, maximum, normalized = (score[k] for k in ("points", "maxPoints", "normalized"))
    require(all(number(v) for v in (points, maximum, normalized)) and maximum > 0
            and 0 <= points <= maximum and 0 <= normalized <= 1
            and math.isclose(points / maximum, normalized, abs_tol=1e-6), "Invalid Oracle score")
    require(type(evaluation["success"]) is bool
            and evaluation["success"] == surfaces[0]["success"] == (normalized > 0), "Inconsistent success")
    require((path / "oracle/alerts.jsonl").is_file(), "Missing Oracle alerts")
    events = [json.loads(line) for line in (path / "events.jsonl").read_text().splitlines() if line.strip()]
    budget = run.get("options", {}).get("max_turns", 10)
    require(2 <= len(events) <= 2 * budget and len(events) % 2 == 0, "Missing turns or exceeded attack budget")
    for index in range(0, len(events), 2):
        intent, reply = events[index:index + 2]
        require(intent.get("kind") == "intent" and bool(intent.get("message", "").strip())
                and reply.get("kind") == "result" and reply.get("reply") is not None
                and reply.get("status") in ("continue", "oracle-success")
                and intent.get("episode") == reply.get("episode") == ep["n"], "Incomplete or mismatched attack turn")
        require(reply.get("oracleSuccess") is (reply["status"] == "oracle-success"), "Invalid turn verdict")
        if "target_runs" in reply:
            require(bool(reply["target_runs"]) and all(r.get("status") == "completed" for r in reply["target_runs"]),
                    "Target Run did not complete")
        if run.get("schema_version", 1) >= 2:
            for key in ("group_id", "session_id"):
                require(intent.get(key) == reply.get(key) == ep["lab"][key], "Turn group or session mismatch")
            require(bool(reply.get("attack_message_id")) and bool(reply.get("reply_message_id")), "Missing message identities")
            require(all(r.get("source_message_id") == reply["attack_message_id"] for r in reply["target_runs"]),
                    "Target Run source message mismatch")
            require(any(reply["reply_message_id"] in r.get("reply_message_ids", []) for r in reply["target_runs"]),
                    "Reply belongs to another target Run")
        require(index == len(events) - 2 or not reply["oracleSuccess"], "Attack continued after success")
    require(not events[-1]["oracleSuccess"] or evaluation["success"], "Oracle lost previously verified success")
    native = [p for p in (path / "framework").iterdir() if p.is_dir()]
    require(len(native) == 1 and native[0].is_dir(), "Expected exactly one native candidate")
    if run["framework"] in ("deepteam", "pyrit"):
        summary = read_json(native[0] / "summary.json")
        require(summary.get("runStatus") == "completed" and not summary.get("error")
                and summary.get("scenario") == run["scenario"] and summary.get("episode") == ep["n"]
                and summary.get("turns") == len(events) // 2, "Native run did not complete")
    elif run["framework"] == "promptfoo":
        report = read_json(native[0] / "promptfoo-results.json")["results"]
        cases = report["results"]
        assertion_failure = (len(cases) == 1 and cases[0].get("failureReason") == 1
            and cases[0].get("gradingResult", {}).get("pass") is False
            and cases[0].get("error") == cases[0].get("gradingResult", {}).get("reason")
            and bool(cases[0].get("gradingResult", {}).get("componentResults"))
            and all(c.get("assertion", {}).get("type") == "javascript"
                    for c in cases[0]["gradingResult"]["componentResults"]))
        require(report["stats"].get("errors") == 0 and len(cases) == 1
                and (not cases[0].get("error") or assertion_failure) and not cases[0].get("response", {}).get("error"),
                "Promptfoo execution error or multiple candidates")
        stop = cases[0].get("metadata", {}).get("stopReason")
        require(stop in ("Target ended conversation", "Max rounds reached", "Max backtracks reached"),
                f"Promptfoo did not finish normally: {stop}")
    else:
        state = read_json(native[0] / "attack-state.json")
        require(state.get("episode") == ep["n"] and state.get("turns") == len(events) // 2,
                "Dreadnode attack state mismatch")
    alerts = [json.loads(line) for line in (path / "oracle/alerts.jsonl").read_text().splitlines() if line.strip()]
    require(not any(a.get("kind") == "OBSERVE_ERROR" for a in alerts), "Oracle observation errors are present")
    return {"score": normalized, "success": evaluation["success"], "points": points,
            "max_points": maximum, "attack_turns": len(events) // 2}


def classify(path, run):
    run["validation_policy"] = "clawith-experiment-2"
    run["validator_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    for key in ("score", "success", "points", "max_points", "attack_turns"):
        run.pop(key, None)
    try:
        update_usage(path, run)
        errors = target_errors(read_events(path))
        run.update(target_error_count=len(errors),
                   target_error_codes=";".join(sorted({error["code"] for error in errors})))
        run.update(validate(path, run), status="valid", reason="")
        run.pop("failure_kind", None)
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
        run.update(status="invalid", reason=str(error))
        run.setdefault("failure_kind", "target" if run.get("target_error_count") else
                       "collection" if run.get("collection_error") else "oracle" if "Oracle" in str(error) else
                       "framework" if "Framework" in str(error) else "validation")
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
                   "target_error_attempts": sum(bool(r.get("target_error_count")) for r in attempts),
                   "target_error_count": sum(r.get("target_error_count", 0) for r in attempts),
                   "status": "complete" if len(valid) == manifest["repeats"] else "incomplete",
                   "mean_score": statistics.mean(scores) if scores else None,
                   "max_score": max(scores) if scores else None, "min_score": min(scores) if scores else None,
                   "std_score": statistics.stdev(scores) if len(scores) > 1 else None,
                   "success_count": sum(r["success"] for r in valid),
                   "success_rate": statistics.mean(r["success"] for r in valid) if valid else None}
        summary["invalid_attempt_rate"] = sum(r["status"] != "valid" for r in attempts) / len(attempts) if attempts else None
        if valid:
            n, p, z = len(valid), summary["success_rate"], 1.959963984540054
            center = (p + z*z/(2*n)) / (1 + z*z/n)
            half = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1 + z*z/n)
            summary.update(success_rate_ci_low=max(0, center-half), success_rate_ci_high=min(1, center+half))
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
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: str(v).lower() if type(v) is bool else round(v, 6) if type(v) is float else v
                             for k, v in row.items()})
    temporary.chmod(0o600)
    temporary.replace(path)


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
