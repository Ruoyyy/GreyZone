"""Offline evidence validation and stable CSV reports; framework grades never score."""
import argparse
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import statistics

from experiment_common import digest, read_json, write_json
from redteam.observer import (DELETED_CHANNEL_REPLY, channel_deletion,
                              classify as target_status, public_replies)
from redteam.usage import summarize as usage_summary

RUN_FIELDS = ("scenario,repeat,attempt,status,score,success,points,max_points,reason,path,"
    "failure_kind,collection_status,diagnostics_status,attack_turns,target_error_count,target_error_codes,"
    "started_at,finished_at,duration_seconds,reset_duration_seconds,framework_duration_seconds,oracle_wait_seconds,"
    "token_usage_status,token_usage_source,framework_input_tokens,framework_output_tokens,framework_total_tokens").split(",")
SUMMARY_FIELDS = ("scenario,planned_runs,valid_runs,invalid_attempts,target_error_attempts,target_error_count,status,"
    "mean_score,max_score,min_score,std_score,success_count,success_rate,success_rate_ci_low,success_rate_ci_high,"
    "invalid_attempt_rate,mean_duration_seconds,total_duration_seconds,mean_framework_duration_seconds,"
    "total_framework_duration_seconds,mean_framework_tokens,total_framework_tokens,token_usage_attempts,"
    "valid_token_runs,approximate_token_attempts").split(",")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def utc(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0, "Expected UTC timestamps")
    return stamp


def validate(path, run, manifest):
    require(not run.get("execution_error"), run.get("execution_error"))
    require(not run.get("collection_error"), run.get("collection_error"))
    require(run.get("framework_returncode", run.get("returncode")) == 0, "Runner execution did not complete")
    native = path / "framework/native"
    config, state, marker = (read_json(native / name) for name in ("config.json", "attack-state.json", "completion.json"))
    ep, result = read_json(path / "oracle/episode.json"), read_json(path / "oracle/result.json")
    evaluation = result["evaluation"]
    require(evaluation["scorePolicyVersion"] == manifest["provenance"]["oracle_policy"], "Oracle scoring policy mismatch")
    spec = manifest["scenario_specs"][run["scenario"]]
    require(config["runId"] == ep["redteam"]["runId"] == evaluation["runId"] == run["run_id"], "Run identity mismatch")
    require(config["framework"] == run["framework"] == manifest["framework"], "Framework mismatch")
    require(config["scenario"] == ep["scenario"]["id"] == evaluation["scenarioId"] == run["scenario"], "Scenario mismatch")
    require(evaluation["application"] == "commonly" and result["environment"] == "commonly", "Wrong application")
    require(str(ep["n"]) == str(result["episode"]) == evaluation["episodeId"] == str(state["episode"]), "Episode mismatch")
    require(ep["state"] == "closed" and evaluation["status"] == "completed" and not evaluation.get("errors"), "Oracle did not complete")
    require(marker.get("frameworkStatus") == marker.get("oracleStatus") == "completed" and not marker.get("error"), "Native run did not complete")
    require(config.get("baseline") == ep.get("baseline") == run.get("baseline"), "Baseline mismatch")
    require(config["model"] == ep["redteam"]["model"] == evaluation["provenance"]["attackerModel"] == run["attacker_model"], "Attacker model mismatch")
    require(config["victimModel"] == evaluation["provenance"]["victimModel"] == run["victim_model"], "Victim model mismatch")
    require(evaluation["provenance"]["revision"] == run["revision"], "Revision mismatch")
    require(config["maxTurns"] == state["maxTurns"] == ep["redteam"]["maxTurns"] == spec["maxTurns"], "Scenario budget mismatch")
    wait = run["options"]["oracle_wait"]
    require(config["oracleWaitSeconds"] == state["waitSeconds"] == ep["redteam"]["waitSeconds"] == wait, "Oracle wait mismatch")
    require(utc(run["started_at"]) <= utc(evaluation["startedAt"]) <= utc(evaluation["closedAt"]) <= utc(run["finished_at"]), "Oracle timestamps outside attempt")
    surfaces = [s for s in evaluation["surfaces"] if s.get("inScope") is True]
    require([s["id"] for s in surfaces] == spec["oracleSuccessSurfaces"] == ep["scenario"]["successSurfaces"], "Wrong Oracle surface")
    score = surfaces[0]["score"]
    points, maximum, normalized = (score[k] for k in ("points", "maxPoints", "normalized"))
    require(all(number(v) for v in (points, maximum, normalized)) and maximum > 0 and 0 <= points <= maximum
            and 0 <= normalized <= 1 and math.isclose(points / maximum, normalized, abs_tol=1e-6), "Invalid Oracle score")
    require(type(evaluation["success"]) is bool and evaluation["success"] == surfaces[0]["success"] == (normalized > 0), "Inconsistent success")
    settlement = read_json(native / "settlement.json")
    events = state["events"]
    require(type(state["turns"]) is int and len(events) == state["turns"] and 1 <= len(events) <= spec["maxTurns"], "Missing turns or exceeded budget")
    ids = [e.get("attackMessageId") for e in events]
    require(all(isinstance(i, str) and i.isdigit() for i in ids) and len(set(ids)) == len(ids), "Missing or duplicate accepted messages")
    require(ids == ep["redteam"]["messageIds"] == settlement["messageIds"] and settlement["status"] == "settled"
            and set(settlement["rows"]) == set(ids) and settlement["waitSeconds"] == wait, "Missing final settlement evidence")
    for index, event in enumerate(events, 1):
        require(event["turn"] == event["turns"] == index and bool(event["message"].strip())
                and event["status"] in {"continue", "oracle-success"} and not event.get("error"), "Incomplete attack turn")
        require(event["oracleSuccess"] is (event["status"] == "oracle-success"), "Inconsistent turn verdict")
        require(index == len(events) or not event["oracleSuccess"], "Attack continued after success")
        identifier = event["attackMessageId"]
        before = read_json(native / f"target-{index}.json")
        for row in (before, settlement["rows"][identifier]):
            require(target_status(row) == "completed", "Target execution failed or did not settle")
            require(all(str(e.get("payload", {}).get("messageId")) == identifier
                        and str(e.get("podId")) == ep["pods"]["shared"] for e in row["events"]), "Target event belongs to another attack")
            event_ids = {str(e["_id"]) for e in row["events"]}
            require(all(str(r.get("triggerEventId")) in event_ids for r in row["runs"]), "Target Run belongs to another event")
            require(all(r.get("status") in {"succeeded", "failed"} for r in row["runs"]), "Unknown target terminal state")
        messages = read_json(native / f"public-messages-{index}.json")
        replies = public_replies(before, messages, identifier)
        require([str(m["id"]) for m in replies] == event["replyMessageIds"], "Reply does not match the accepted attack")
        reply = "\n\n".join(str(m.get("content", "")) for m in replies) or "[Target finished without a public text reply.]"
        if event.get("stopReason") == "shared-pod-deleted":
            channel = read_json(native / f"channel-state-{index}.json")
            require(index == len(events) and state.get("stopped") is True
                    and marker.get("stopReason") == "shared-pod-deleted"
                    and channel.get("podId") == ep["pods"]["shared"] and channel.get("exists") is False
                    and utc(evaluation["startedAt"]) <= utc(channel["observedAt"]) <= utc(evaluation["closedAt"])
                    and not messages and not event["replyMessageIds"]
                    and channel_deletion(before, ep["pods"]["shared"], identifier)
                    and channel_deletion(settlement["rows"][identifier], ep["pods"]["shared"], identifier),
                    "Missing causal channel deletion evidence")
            if run["framework"] in {"deepteam", "pyrit"}:
                termination = read_json(native / "termination.json")
                require(termination == {"reason": "shared-pod-deleted", "turn": index,
                                        "framework": run["framework"]}, "Missing controlled SDK termination")
            reply = DELETED_CHANNEL_REPLY
        require(reply == event["reply"], "Public reply text mismatch")
    require(not events[-1]["oracleSuccess"] or evaluation["success"], "Final Oracle lost verified success")
    alerts = [json.loads(line) for line in (path / "oracle/alerts.jsonl").read_text().splitlines() if line.strip()]
    require(not any(a.get("kind") == "OBSERVE_ERROR" for a in alerts), "Oracle observation error")
    if run["framework"] == "promptfoo":
        report = read_json(native / "promptfoo-results.json")["results"]
        require(report["stats"].get("errors") == 0 and report["stats"].get("successes", 0) + report["stats"].get("failures", 0) == 1, "Promptfoo execution error or multiple candidates")
        cases = report["results"]
        require(len(cases) == 1 and not cases[0].get("response", {}).get("error"), "Promptfoo target error")
        case, grade = cases[0], cases[0].get("gradingResult", {})
        assertion_failure = (case.get("failureReason") == 1 and grade.get("pass") is False
            and case.get("error") == grade.get("reason") and bool(grade.get("componentResults"))
            and all(c.get("assertion", {}).get("type") == "javascript" for c in grade["componentResults"]))
        require(not case.get("error") or assertion_failure, "Promptfoo native execution error")
    return {"score": normalized, "success": evaluation["success"], "points": points,
            "max_points": maximum, "attack_turns": len(events), "oracle_policy": evaluation["scorePolicyVersion"]}


def classify(path, run, manifest):
    for key in ("score", "success", "points", "max_points", "attack_turns"):
        run.pop(key, None)
    native = path / "framework/native"
    try:
        usage = usage_summary(native, run["framework"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        usage = {"status": "unavailable", "error": str(error)}
    write_json(path / "usage.json", usage)
    run.update(token_usage_status=usage["status"], token_usage_source=usage.get("source"))
    for key in ("input", "output", "total"):
        value = usage.get(key + "_tokens")
        run["framework_" + key + "_tokens"] = value if number(value) and value >= 0 else None
    errors = set()
    failures = 0
    for file in native.glob("target-*.json"):
        try:
            row = read_json(file)
            failed = [r for r in row.get("runs", []) if r.get("errorKind") not in {None, "guardrail_blocked"}
                      or r.get("status") in {"failed", "interrupted"} and r.get("errorKind") != "guardrail_blocked"]
            failures += len(failed)
            errors.update(r.get("errorKind") or "target_execution_error" for r in failed)
            if target_status(row) == "error" and not failed:
                failures += 1
                errors.add("target_execution_error")
        except (ValueError, KeyError, TypeError):
            failures += 1
            errors.add("target_evidence_invalid")
    if not failures and (native / "attack-state.json").is_file():
        try:
            for event in read_json(native / "attack-state.json").get("events", []):
                if event.get("status") == "invalid":
                    failures += 1
                    errors.add("target_timeout" if "TimeoutError" in event.get("error", "") else "target_turn_error")
        except (ValueError, KeyError, TypeError):
            failures += 1
            errors.add("target_evidence_invalid")
    run.update(target_error_count=failures, target_error_codes=";".join(sorted(errors)),
               validator_sha256=digest(Path(__file__)), validation_policy="commonly-experiment-1")
    try:
        run.update(validate(path, run, manifest), status="valid", reason="")
        run.pop("failure_kind", None)
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
        run.update(status="invalid", reason=str(error))
    write_json(path / "run.json", run)
    return run


def wilson(successes, count):
    if not count:
        return None, None
    z, fraction = 1.959963984540054, successes / count
    denominator = 1 + z * z / count
    center = (fraction + z * z / (2 * count)) / denominator
    half = z * math.sqrt(fraction * (1 - fraction) / count + z * z / (4 * count * count)) / denominator
    return max(0, center - half), min(1, center + half)


def aggregate(rows, manifest):
    result = []
    for scenario in manifest["scenarios"]:
        attempts = [r for r in rows if r["scenario"] == scenario]
        valid = [r for r in attempts if r["status"] == "valid"]
        require(len({r["repeat"] for r in valid}) == len(valid), "Duplicate valid repetition")
        scores = [r["score"] for r in valid]
        successes = sum(r["success"] for r in valid)
        low, high = wilson(successes, len(valid))
        row = {"scenario": scenario, "planned_runs": manifest["repeats"], "valid_runs": len(valid),
            "invalid_attempts": len(attempts) - len(valid), "status": "complete" if len(valid) == manifest["repeats"] else "incomplete",
            "target_error_attempts": sum(bool(r.get("target_error_count")) for r in attempts),
            "target_error_count": sum(r.get("target_error_count", 0) for r in attempts),
            "mean_score": statistics.mean(scores) if scores else None, "max_score": max(scores) if scores else None,
            "min_score": min(scores) if scores else None, "std_score": statistics.stdev(scores) if len(scores) > 1 else None,
            "success_count": successes, "success_rate": successes / len(valid) if valid else None,
            "success_rate_ci_low": low, "success_rate_ci_high": high,
            "invalid_attempt_rate": (len(attempts) - len(valid)) / len(attempts) if attempts else None}
        for column, label in (("duration_seconds", "duration_seconds"), ("framework_duration_seconds", "framework_duration_seconds"),
                              ("framework_total_tokens", "framework_tokens")):
            known_valid = [r[column] for r in valid if number(r.get(column))]
            known_all = [r[column] for r in attempts if number(r.get(column))]
            row["mean_" + label] = statistics.mean(known_valid) if known_valid else None
            row["total_" + label] = sum(known_all) if known_all else None
        row.update(token_usage_attempts=sum(number(r.get("framework_total_tokens")) for r in attempts),
                   valid_token_runs=sum(number(r.get("framework_total_tokens")) for r in valid),
                   approximate_token_attempts=sum(r.get("token_usage_status") == "approximate" for r in attempts))
        result.append(row)
    return result


def write_csv(path, fields, rows):
    temporary = path.with_suffix(".csv.tmp")
    with temporary.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: round(v, 6) if type(v) is float else v for k, v in row.items()})
    temporary.chmod(0o600)
    temporary.replace(path)


def collect(batch):
    manifest = read_json(batch / "manifest.json")
    rows = []
    for file in sorted(batch.glob("*/repeat-*/attempt-*/run.json")):
        row = read_json(file)
        require(row["scenario"] in manifest["scenarios"] and type(row["repeat"]) is int
                and 1 <= row["repeat"] <= manifest["repeats"], "Attempt outside frozen plan")
        row = classify(file.parent, row, manifest)
        row["path"] = str(file.parent.relative_to(batch))
        rows.append(row)
    summaries = aggregate(rows, manifest)
    write_csv(batch / "runs.csv", RUN_FIELDS, rows)
    write_csv(batch / "summary.csv", SUMMARY_FIELDS, summaries)
    return all(r["status"] == "complete" for r in summaries)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", type=Path)
    args = parser.parse_args()
    collect(args.batch.resolve())
    print(f"Updated {args.batch}/runs.csv and summary.csv (offline)")
