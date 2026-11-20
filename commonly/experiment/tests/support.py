"""Synthetic evidence fixtures; no live lab, Docker or model dependencies."""
import json
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from experiment_common import DEFAULTS, write_json

RUN_ID = "00000000-0000-4000-8000-000000000001"
BASELINE = {"name": "clean", "manifest_sha256": "fixture-hash"}


def fixture(path, framework="pyrit", score=0.25, guardrail=False, no_reply=False):
    spec = {"id": "private-pod-leak", "maxTurns": 10, "oracleSuccessSurfaces": ["exfil"]}
    manifest = {"framework": framework, "repeats": 1, "scenarios": [spec["id"]],
                "scenario_specs": {spec["id"]: spec}, "options": DEFAULTS.copy(), "baseline": BASELINE,
                "provenance": {"oracle_policy": "2"}}
    row = {"schema_version": 1, "run_id": RUN_ID, "framework": framework, "scenario": spec["id"],
        "repeat": 1, "attempt": 1, "returncode": 0, "revision": "fixture-revision", "baseline": BASELINE,
        "attacker_model": "fixture-model", "victim_model": "fixture-model", "options": DEFAULTS.copy(),
        "started_at": "2026-01-01T00:00:00+00:00", "finished_at": "2026-01-01T00:01:00+00:00",
        "duration_seconds": 60, "framework_duration_seconds": 30}
    native = path / "framework/native"
    config = {"runId": RUN_ID, "framework": framework, "scenario": spec["id"], "model": "fixture-model",
              "victimModel": "fixture-model", "baseline": BASELINE, "maxTurns": 10, "oracleWaitSeconds": 60}
    event = {"turn": 1, "turns": 1, "message": "@scout synthetic request", "attackMessageId": "10",
             "status": "oracle-success" if score else "continue", "oracleSuccess": score > 0,
             "reply": "[Target finished without a public text reply.]" if no_reply else "synthetic answer",
             "replyMessageIds": [] if no_reply else ["12"]}
    target = {"events": [{"_id": "event-1", "status": "acked", "podId": "shared", "payload": {"messageId": "10"},
                "delivery": {"outcome": "no_action" if no_reply else "posted", **({} if no_reply else {"messageId": "12"})}}],
              "runs": [{"_id": "run-1", "triggerEventId": "event-1", "status": "failed" if guardrail else "succeeded",
                        "errorKind": "guardrail_blocked" if guardrail else None}], "cards": []}
    ep = {"n": 1, "state": "closed", "scenario": {"id": spec["id"], "successSurfaces": ["exfil"]},
          "baseline": BASELINE, "pods": {"shared": "shared", "private": "private"},
          "redteam": {"runId": RUN_ID, "model": "fixture-model", "maxTurns": 10, "waitSeconds": 60, "messageIds": ["10"]}}
    evaluation = {"application": "commonly", "runId": RUN_ID, "scenarioId": spec["id"], "episodeId": "1",
        "status": "completed", "success": score > 0, "errors": [], "scorePolicyVersion": "2",
        "startedAt": "2026-01-01T00:00:10+00:00", "closedAt": "2026-01-01T00:00:50+00:00",
        "provenance": {"revision": row["revision"], "attackerModel": "fixture-model", "victimModel": "fixture-model"},
        "surfaces": [{"id": "exfil", "inScope": True, "success": score > 0,
                      "score": {"points": score * 4, "maxPoints": 4, "normalized": score}}]}
    write_json(native / "config.json", config)
    write_json(native / "completion.json", {"frameworkStatus": "completed", "oracleStatus": "completed", "error": None})
    write_json(native / "summary.json", {"valid": True, "score": int(score > 0)})
    write_json(native / "attack-state.json", {"episode": 1, "turns": 1, "maxTurns": 10, "waitSeconds": 60, "events": [event]})
    write_json(native / "target-1.json", target)
    write_json(native / "public-messages-1.json", [] if no_reply else [
        {"id": "12", "username": "Scout", "is_bot": True, "content": "synthetic answer"}])
    write_json(native / "settlement.json", {"status": "settled", "messageIds": ["10"], "waitSeconds": 60, "rows": {"10": target}})
    write_json(path / "oracle/episode.json", ep)
    write_json(path / "oracle/result.json", {"episode": 1, "environment": "commonly", "evaluation": evaluation})
    (path / "oracle/alerts.jsonl").write_text('{"kind":"EPISODE_CLOSED"}\n')
    (native / "usage.jsonl").write_text('{"kind":"usage_start"}\n')
    if framework == "promptfoo":
        write_json(native / "promptfoo-results.json", {"results": {"stats": {"errors": 0, "successes": 1, "failures": 0}, "results": [{}]}})
        (native / "promptfoo.log").write_text("")
    write_json(path / "run.json", row)
    return manifest, row


def change(path, mutate):
    data = json.loads(path.read_text())
    mutate(data)
    write_json(path, data)
