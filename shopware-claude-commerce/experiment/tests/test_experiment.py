"""Offline checks of scoring, recovery, process isolation and token accounting."""
import csv
import io
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import experiment_common as common
import run
import run_all
import summarize
from progress import Progress
from usage import framework_usage


@pytest.fixture
def saved(tmp_path):
    batch = tmp_path / "batch"
    path = batch / "buyer-misdirection/repeat-01/attempt-01"
    manifest = {"framework": "promptfoo", "scenarios": ["buyer-misdirection"], "repeats": 1}
    common.write_json(batch / "manifest.json", manifest)
    metadata = {"framework": "promptfoo", "scenario": "buyer-misdirection", "repeat": 1,
                "attempt": 1, "run_id": "test-run", "revision": "abc", "victim_model": "victim",
                "attacker_model": "attacker", "returncode": 100,
                "started_at": "2026-11-16T00:00:00+00:00", "finished_at": "2026-11-16T00:00:03+00:00",
                "duration_seconds": 3, "framework_duration_seconds": 2}
    common.write_json(path / "run.json", metadata)
    common.write_json(path / "framework/task.json", {"status": "completed", "scenario": "buyer-misdirection",
        "run_id": "test-run", "episode": 1, "attacks": [{"channel": "create_product"}]})
    for filename in ("oracle/before.json", "oracle/after.json", "framework/victim-reply.json"):
        common.write_json(path / filename, {})
    evaluation = {"application": "shopware-claude-commerce", "scenarioId": "buyer-misdirection",
        "runId": "test-run", "episodeId": "1", "status": "completed", "errors": [], "success": False,
        "startedAt": "2026-11-16T00:00:01Z", "closedAt": "2026-11-16T00:00:02Z",
        "provenance": {"revision": "abc", "victimModel": "victim", "attackerModel": "attacker"},
        "surfaces": [{"id": "purchase", "inScope": True, "success": False,
                      "score": {"points": 0, "maxPoints": 1, "normalized": 0}}]}
    common.write_json(path / "oracle/result.json", {"evaluation": evaluation})
    (path / "oracle/alerts.jsonl").touch()
    native = {"results": {"stats": {"errors": 0}, "results": [
        {"error": "Safety assertion failed", "response": {"metadata": {"resultPath": str(path / "oracle/result-1.json")}}}]}}
    common.write_json(path / "framework/native/results.json", native)
    return batch, path, metadata, evaluation


def test_oracle_zero_is_valid_and_headers_are_fixed(saved):
    batch, path, metadata, _ = saved
    assert summarize.collect(batch)
    row = next(csv.DictReader((batch / "runs.csv").open()))
    assert list(row) == summarize.RUN_FIELDS
    assert row["score"] == "0" and row["success"] == "false" and row["status"] == "valid"
    summary = next(csv.DictReader((batch / "summary.csv").open()))
    assert list(summary) == summarize.SUMMARY_FIELDS
    assert summary["std_score"] == "" and summary["mean_framework_tokens"] == ""


@pytest.mark.parametrize("change", ["identity", "surface", "status", "score", "timestamp", "provenance", "success"])
def test_bad_oracle_is_invalid_and_never_zero(saved, change):
    batch, path, metadata, evaluation = saved
    if change == "identity": evaluation["runId"] = "old-run"
    if change == "surface": evaluation["surfaces"][0]["id"] = "exfil"
    if change == "status": evaluation["status"] = "error"
    if change == "score": evaluation["surfaces"][0]["score"]["normalized"] = 1
    if change == "timestamp": evaluation["startedAt"] = "promptfoo"
    if change == "provenance": evaluation["provenance"]["revision"] = "wrong"
    if change == "success": evaluation["success"] = True
    common.write_json(path / "oracle/result.json", {"evaluation": evaluation})
    assert not summarize.collect(batch)
    row = next(csv.DictReader((batch / "runs.csv").open()))
    assert row["score"] == "" and row["reason"] and row["status"] == "invalid"


def test_framework_failure_invalidates_even_completed_oracle(saved):
    _, path, metadata, _ = saved
    metadata["returncode"] = 1
    assert summarize.classify(path, metadata)["status"] == "invalid"
    assert "score" not in metadata


def test_aggregate_uses_valid_means_and_all_attempt_totals():
    manifest = {"scenarios": ["buyer-misdirection"], "repeats": 2}
    rows = [{"scenario": "buyer-misdirection", "repeat": 1, "status": "valid", "score": 0, "success": False,
             "duration_seconds": 10, "framework_total_tokens": 100},
            {"scenario": "buyer-misdirection", "repeat": 2, "status": "invalid", "duration_seconds": 20}]
    summary = summarize.aggregate(rows, manifest)[0]
    assert summary["mean_duration_seconds"] == 10 and summary["total_duration_seconds"] == 30
    assert summary["mean_score"] == 0 and summary["invalid_attempts"] == 1
    assert summary["token_usage_attempts"] == 1 and summary["status"] == "incomplete"
    with pytest.raises(ValueError, match="Duplicate"):
        summarize.aggregate(rows + [rows[0]], manifest)


def test_promptfoo_token_buckets_are_not_double_counted(tmp_path):
    common.write_json(tmp_path / "framework/native/results.json", {"results": {"stats": {"tokenUsage": {
        "total": 9999, "prompt": 9999, "assertions": {"prompt": 10, "completion": 2, "total": 12},
        "generation": {"prompt": 20, "completion": 3, "total": 23}}}}})
    usage = framework_usage(tmp_path, "promptfoo")
    assert usage["total_tokens"] == 35 and usage["status"] == "reported"


def test_dreadnode_abbreviated_tokens_are_approximate(tmp_path):
    (tmp_path / "console.log").write_text("done · 2.4k tokens · complete\n")
    usage = framework_usage(tmp_path, "dreadnode")
    assert usage["total_tokens"] == 2400 and usage["status"] == "approximate"
    assert usage["input_tokens"] is None


def test_exclusive_task_marker_rejects_second_victim(tmp_path, monkeypatch):
    from redteam.experiment_support import evidence
    monkeypatch.setenv("SHOPWARE_EXPERIMENT_DIR", str(tmp_path))
    evidence("framework/task.json", {"status": "running"}, exclusive=True)
    with pytest.raises(FileExistsError):
        evidence("framework/task.json", {"status": "running"}, exclusive=True)


def test_lock_is_inherited_through_node_and_python(tmp_path, monkeypatch):
    import redteam.experiment_support as support
    lock = tmp_path / "campaign.lock"
    monkeypatch.setattr(support, "LOCK_PATH", lock)
    with support.campaign_lock() as descriptor:
        # The child imports the real module; use the same temporary lock path.
        code = ("import os;from pathlib import Path;import redteam.experiment_support as s;"
                f"s.LOCK_PATH=Path({str(lock)!r});"
                "c=s.campaign_lock();c.__enter__();print('inherited');c.__exit__(None,None,None)")
        node = "const {spawnSync}=require('node:child_process');const r=spawnSync(process.argv[1],['-c',process.argv[2]],{env:{...process.env,SHOPWARE_CAMPAIGN_LOCK_FD:'3'},stdio:['ignore','pipe','pipe',Number(process.env.SHOPWARE_CAMPAIGN_LOCK_FD)],encoding:'utf8'});process.stdout.write(r.stdout);process.stderr.write(r.stderr);process.exit(r.status);"
        result = subprocess.run(["node", "-e", node, sys.executable, code], cwd=common.ROOT,
            env={**os.environ, "SHOPWARE_CAMPAIGN_LOCK_FD": str(descriptor)}, pass_fds=(descriptor,),
            capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert "inherited" in result.stdout
        # A separate open-file description must still be excluded.
        other = subprocess.run([sys.executable, "-c", code], cwd=common.ROOT,
            env={k: v for k, v in os.environ.items() if k != "SHOPWARE_CAMPAIGN_LOCK_FD"}, capture_output=True, text=True)
        assert other.returncode != 0 and "Another Shopware campaign" in other.stderr


def test_execute_streams_without_newlines_and_does_not_read_stdin(tmp_path):
    progress = Progress(tmp_path / "experiment.log", output=io.StringIO())
    descriptor = os.open(tmp_path / "lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        code = "import os,sys;assert sys.stdin.read()=='';os.write(1,b'partial');os.write(2,b'error')"
        result = run.execute([sys.executable, "-c", code], tmp_path, os.environ.copy(), descriptor,
                             progress, tmp_path / "console.log", 5)
        assert result == 0 and (tmp_path / "console.log").read_text() == "partialerror"
        assert progress.output.getvalue() == "partialerror"
    finally:
        os.close(descriptor)


def test_successful_reset_keeps_started_hosts_running(tmp_path):
    progress = Progress(tmp_path / "experiment.log", output=io.StringIO())
    descriptor = os.open(tmp_path / "lock", os.O_CREAT | os.O_RDWR, 0o600)
    code = (f"import os\ntry: os.fstat({descriptor})\nexcept OSError: pass\nelse: raise AssertionError('Reset inherited campaign lock')\n"
            "import pathlib,subprocess,sys;"
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],"
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
            "pathlib.Path('host.pid').write_text(str(p.pid))")
    pid = None
    try:
        assert run.execute([sys.executable, "-c", code], tmp_path, os.environ.copy(), None,
                           progress, tmp_path / "console.log", 5, keep_children=True) == 0
        pid = int((tmp_path / "host.pid").read_text())
        os.kill(pid, 0)
    finally:
        if pid is not None:
            os.kill(pid, signal.SIGKILL)
        os.close(descriptor)


def test_reset_failure_is_saved_without_launching_framework(saved, monkeypatch):
    batch, _, metadata, _ = saved
    manifest = {**common.read_json(batch / "manifest.json"), "revision": "abc", "victim_models": {"buyer": "victim"}, "attacker_model": "attacker"}
    monkeypatch.setattr(run, "reset", Mock(side_effect=RuntimeError("Reset failed")))
    execute = Mock()
    monkeypatch.setattr(run, "execute", execute)
    args = run.parser().parse_args(["--framework", "promptfoo"])
    assert not run.attempt(batch, manifest, "buyer-misdirection", 1, 3, args)
    execute.assert_not_called()
    attempt = common.read_json(batch / "buyer-misdirection/repeat-01/attempt-02/run.json")
    assert "Reset failed" in attempt["execution_error"] and attempt["status"] == "invalid"


def test_archive_preserves_evidence_references(tmp_path):
    path = tmp_path / "attempt"
    result = {"alertsFile": "alerts-1.jsonl", "evaluation": {"evidenceFiles": ["alerts-1.jsonl"]}}
    common.write_json(path / "oracle/result-1.json", result)
    (path / "oracle/alerts-1.jsonl").write_text("{}\n")
    run.archive(path, "promptfoo", run.native_directories("promptfoo"))
    archived = common.read_json(path / "oracle/result.json")
    assert archived["alertsFile"] == "alerts.jsonl"
    assert all((path / "oracle" / filename).is_file() for filename in archived["evaluation"]["evidenceFiles"])
    assert not (path / "oracle/result-1.json").exists()


def test_resume_skips_valid_repeat_and_uses_saved_plan(saved, monkeypatch):
    batch, _, _, _ = saved
    parent = batch.parent / "promptfoo"
    parent.mkdir()
    target = parent / batch.name
    batch.rename(target)
    native_path = target / "buyer-misdirection/repeat-01/attempt-01/framework/native/results.json"
    native = common.read_json(native_path)
    native["results"]["results"][0]["response"]["metadata"]["resultPath"] = str(native_path.parents[2] / "oracle/result-1.json")
    common.write_json(native_path, native)
    manifest = {**common.read_json(target / "manifest.json"), "configuration": {}, "image_id": "image"}
    common.write_json(target / "manifest.json", manifest)
    monkeypatch.setattr(run, "RESULTS", target.parents[1])
    monkeypatch.setattr(run, "preflight", Mock())
    monkeypatch.setattr(run, "check_scenarios", Mock())
    monkeypatch.setattr(run, "configuration", lambda: {})
    monkeypatch.setattr(run, "image_id", lambda: "image")
    from contextlib import contextmanager
    @contextmanager
    def lock():
        yield 3
    monkeypatch.setattr(run, "campaign_lock", lock)
    attempt = Mock()
    monkeypatch.setattr(run, "attempt", attempt)
    assert run.main(["--framework", "promptfoo", "--resume", target.name]) == 0
    attempt.assert_not_called()


def test_run_all_continues_and_resumes_each_framework(tmp_path, monkeypatch):
    monkeypatch.setattr(run_all, "RESULTS", tmp_path)
    monkeypatch.setattr(sys, "argv", ["run_all.py", "--scenario", "buyer-misdirection", "--repeats", "1"])
    child = Mock(side_effect=[1, 0, 0, 1])
    monkeypatch.setattr(run_all.subprocess, "call", child)
    assert run_all.main() == 1 and child.call_count == 4
    for call in child.call_args_list:
        assert call.kwargs["stdin"] == subprocess.DEVNULL
    series_path = next(tmp_path.glob("series-*.json"))
    series_id = series_path.stem.removeprefix("series-")
    series = common.read_json(series_path)
    for framework, batch in series["batches"].items():
        common.write_json(tmp_path / framework / batch / "manifest.json", {})
    monkeypatch.setattr(sys, "argv", ["run_all.py", "--resume", series_id])
    child.reset_mock(side_effect=True)
    child.return_value = 0
    assert run_all.main() == 0
    assert all("--resume" in call.args[0] for call in child.call_args_list)
