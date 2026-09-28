# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Run the eval suite against the real model.

    python -m evals.runner --suite shopping|merchant|all --set ci|full \
        --mode replay|live --trials 2 [--model claude-sonnet-5] --report out.json

Every case gets a fresh backend, agent, memory store and session per trial; the
snapshot state is built through the backend (``evals/harness.py``), one turn runs, the
scorers grade the outcome. The report carries the pass rate per case over its trials,
the cache-hit rate and estimated cost per turn, and (for the ci set) the gate verdict
from ``evals/gates.yaml``. The exit code is non-zero when the gate fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from anthropic import APIConnectionError, APIStatusError, APITimeoutError

from . import GATES_PATH
from .backends import (
    BackendImportError,
    MerchantHarness,
    Mode,
    ShoppingHarness,
    anthropic_client,
    build_merchant_harness,
    build_shopping_harness,
    load_project_env,
    project_root,
)
from .cases import Case, coverage_report, load_cases
from .ci import evaluate_gates, load_gates, rates_for, select_ci_cases
from .harness import (
    TURN_TIMEOUT_S,
    SnapshotError,
    TrialResult,
    UnresolvedFixture,
    dump_outcome,
    run_merchant_trial,
    run_shopping_trial,
)

logger = logging.getLogger("evals")

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 529}
MAX_ATTEMPTS = 3
LIVE_WRITES_ENV = "EVALS_ALLOW_LIVE_WRITES"


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=project_root(),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _retryable(error: BaseException) -> bool:
    if isinstance(error, (APIConnectionError, APITimeoutError)):
        return True
    return isinstance(error, APIStatusError) and error.status_code in RETRYABLE_STATUS


def _live_write_blocked(case: Case, mode: Mode) -> str | None:
    """In live mode a case that pre-approves a change would let ``apply_change`` write
    to the shop; that needs an explicit opt-in."""
    if mode != "live":
        return None
    state = case.state
    approved = getattr(state, "approved", None)
    if approved and os.environ.get(LIVE_WRITES_ENV, "0") not in {"1", "true"}:
        return (
            f"live mode: case pre-approves a change; set {LIVE_WRITES_ENV}=1 to allow apply writes"
        )
    return None


async def _run_trial(
    case: Case,
    trial: int,
    mode: Mode,
    *,
    client: Any,
    model: str | None,
    judge_model: str,
    gates: dict[str, Any],
) -> TrialResult:
    attempt = 0
    while True:
        attempt += 1
        harness: ShoppingHarness | MerchantHarness | None = None
        try:
            if case.suite == "shopping":
                harness = await build_shopping_harness(mode)
                effective_model = model or harness.config.model
                return await asyncio.wait_for(
                    run_shopping_trial(
                        harness,
                        case,
                        trial,
                        client=client,
                        model=model,
                        judge_model=judge_model,
                        rates=rates_for(effective_model, gates),
                    ),
                    timeout=TURN_TIMEOUT_S,
                )
            harness = await build_merchant_harness(mode)
            effective_model = model or harness.config.model
            return await asyncio.wait_for(
                run_merchant_trial(
                    harness,
                    case,
                    trial,
                    client=client,
                    model=model,
                    judge_model=judge_model,
                    rates=rates_for(effective_model, gates),
                ),
                timeout=TURN_TIMEOUT_S,
            )
        except UnresolvedFixture as error:
            return TrialResult(
                case.id, trial, False, [], None, error=str(error), error_kind="skipped"
            )
        except SnapshotError as error:
            return TrialResult(
                case.id, trial, False, [], None, error=str(error), error_kind="setup_error"
            )
        except BackendImportError:
            raise
        except Exception as error:  # noqa: BLE001 - reported per trial, never aborts the run
            if _retryable(error) and attempt < MAX_ATTEMPTS:
                delay = 2**attempt + random.uniform(0, 1)
                logger.warning("%s trial %d: %s; retry in %.1fs", case.id, trial, error, delay)
                await asyncio.sleep(delay)
                continue
            return TrialResult(
                case.id,
                trial,
                False,
                [],
                None,
                error=f"{type(error).__name__}: {error}"[:500],
                error_kind="turn_error",
            )
        finally:
            if harness is not None:
                try:
                    await harness.aclose()
                except Exception:  # noqa: BLE001
                    logger.debug("harness close failed", exc_info=True)


async def run_case(
    case: Case,
    *,
    mode: Mode,
    trials: int,
    client: Any,
    model: str | None,
    judge_model: str,
    gates: dict[str, Any],
    verbose: bool = False,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": case.id,
        "title": case.title,
        "suite": case.suite,
        "tags": case.tags,
        "set": case.set,
        "negative_of": case.negative_of,
        "skipped": None,
        "trials": [],
    }
    blocked = case.skip or _live_write_blocked(case, mode)
    if blocked:
        record["skipped"] = blocked
        record["pass_rate"] = None
        record["passed"] = None
        return record
    results: list[TrialResult] = []
    for trial in range(1, trials + 1):
        result = await _run_trial(
            case, trial, mode, client=client, model=model, judge_model=judge_model, gates=gates
        )
        if result.error_kind == "skipped":
            record["skipped"] = result.error
            record["pass_rate"] = None
            record["passed"] = None
            return record
        results.append(result)
        if verbose and result.outcome is not None:
            print(f"--- {case.id} trial {trial}\n{dump_outcome(result.outcome)}", file=sys.stderr)
    record["trials"] = [r.as_dict() for r in results]
    record["pass_rate"] = round(sum(1 for r in results if r.passed) / len(results), 4)
    record["passed"] = all(r.passed for r in results)
    return record


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ran = [r for r in records if not r.get("skipped")]
    trials = [t for r in ran for t in r["trials"]]
    tokens = {
        key: sum(int((t.get("usage") or {}).get(key) or 0) for t in trials)
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
    }
    costs = [float(t["cost_usd"]) for t in trials if t.get("cost_usd") is not None]
    later = [
        float(t["cache_hit_rate"])
        for t in trials
        if int(t.get("trial", 0)) >= 2 and t.get("cache_hit_rate") is not None
    ]
    by_tag: dict[str, dict[str, Any]] = {}
    for record in ran:
        for tag in record["tags"]:
            bucket = by_tag.setdefault(tag, {"cases": 0, "trials": 0, "passed_trials": 0})
            bucket["cases"] += 1
            bucket["trials"] += len(record["trials"])
            bucket["passed_trials"] += sum(1 for t in record["trials"] if t.get("passed"))
    for bucket in by_tag.values():
        bucket["pass_rate"] = (
            round(bucket["passed_trials"] / bucket["trials"], 4) if bucket["trials"] else None
        )
    return {
        "cases": len(records),
        "ran": len(ran),
        "skipped": len(records) - len(ran),
        "passed_cases": sum(1 for r in ran if r.get("passed")),
        "failed_cases": sorted(r["id"] for r in ran if r.get("passed") is False),
        "pass_rate": round(sum(1 for t in trials if t.get("passed")) / len(trials), 4)
        if trials
        else None,
        "by_tag": by_tag,
        "cache_hit_rate_from_trial_2": round(sum(later) / len(later), 4) if later else None,
        "cache_hit_rate_all": round(
            sum(float(t["cache_hit_rate"]) for t in trials if t.get("cache_hit_rate") is not None)
            / max(1, sum(1 for t in trials if t.get("cache_hit_rate") is not None)),
            4,
        )
        if trials
        else None,
        "tokens": tokens,
        "cost_usd_total": round(sum(costs), 4) if costs else None,
        "cost_usd_per_turn": round(sum(costs) / len(costs), 4) if costs else None,
        "judge_errors": sum(
            1 for t in trials for s in t.get("scores") or [] if s.get("judge_error")
        ),
        "setup_errors": sorted(
            {r["id"] for r in ran for t in r["trials"] if t.get("error_kind") == "setup_error"}
        ),
    }


def print_table(report: dict[str, Any]) -> None:
    rows = report["cases"]
    width = max((len(r["id"]) for r in rows), default=20)
    print(f"{'case':{width}s}  pass  cache2  cost/turn  failing")
    for record in rows:
        if record.get("skipped"):
            print(f"{record['id']:{width}s}  skip  -       -          {record['skipped'][:70]}")
            continue
        trials = record["trials"]
        passed = sum(1 for t in trials if t.get("passed"))
        second = next((t for t in trials if int(t.get("trial", 0)) == 2), None)
        cache = (
            f"{second['cache_hit_rate']:.2f}"
            if second and second.get("cache_hit_rate") is not None
            else (
                f"{trials[-1]['cache_hit_rate']:.2f}*"
                if trials and trials[-1].get("cache_hit_rate") is not None
                else "-"
            )
        )
        costs = [float(t["cost_usd"]) for t in trials if t.get("cost_usd") is not None]
        cost = f"${sum(costs) / len(costs):.4f}" if costs else "-"
        failing: list[str] = []
        for t in trials:
            if t.get("error"):
                failing.append(f"t{t['trial']}:{t['error_kind']}")
            failing.extend(
                f"t{t['trial']}:{s['scorer']}" for s in t.get("scores") or [] if not s["passed"]
            )
        print(
            f"{record['id']:{width}s}  {passed}/{len(trials)}   {cache:7s} {cost:10s} {', '.join(failing)[:90]}"
        )
    summary = report["summary"]
    print()
    print(
        f"cases {summary['ran']} ran / {summary['skipped']} skipped; passed {summary['passed_cases']}; "
        f"trial pass rate {summary['pass_rate']}; cache-hit (trial>=2) {summary['cache_hit_rate_from_trial_2']}; "
        f"cache-hit (all) {summary['cache_hit_rate_all']}; cost total ${summary['cost_usd_total']} "
        f"(${summary['cost_usd_per_turn']}/turn)"
    )
    if report.get("gate") is not None:
        gate = report["gate"]
        print(f"gate: {'PASS' if gate['passed'] else 'FAIL'}")
        for failure in gate["failures"]:
            print(f"  - {failure}")


async def run_suite(
    *,
    suite: str,
    case_set: str,
    mode: Mode,
    trials: int,
    model: str | None,
    report_path: Path | None,
    gates: dict[str, Any] | None = None,
    gate: bool | None = None,
    ids: set[str] | None = None,
    tags: set[str] | None = None,
    concurrency: int = 4,
    judge_model: str | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    load_project_env()
    gates = gates or load_gates(GATES_PATH)
    cases = load_cases(suite, case_set, ids=ids, tags=tags)  # type: ignore[arg-type]
    if case_set == "ci":
        cases = select_ci_cases(cases)
    judge = judge_model or str(
        (gates.get("judge") or {}).get("model") or model or "claude-sonnet-5"
    )
    client = anthropic_client(timeout=120.0)
    started = datetime.now(UTC)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def guarded(case: Case) -> dict[str, Any]:
        async with semaphore:
            record = await run_case(
                case,
                mode=mode,
                trials=trials,
                client=client,
                model=model,
                judge_model=judge,
                gates=gates,
                verbose=verbose,
            )
            status = (
                "skip" if record.get("skipped") else ("PASS" if record.get("passed") else "FAIL")
            )
            logger.info("%s %s (%s)", status, case.id, record.get("pass_rate"))
            return record

    records = await asyncio.gather(*(guarded(case) for case in cases))
    records = sorted(records, key=lambda r: r["id"])
    apply_gate = gate if gate is not None else case_set == "ci"
    report: dict[str, Any] = {
        "meta": {
            "suite": suite,
            "set": case_set,
            "mode": mode,
            "trials": trials,
            "model_override": model,
            "models_used": sorted(
                {t["model"] for r in records for t in r.get("trials") or [] if t.get("model")}
            ),
            "judge_model": judge,
            "started_at": started.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "git_sha": _git_sha(),
            "project_root": str(project_root()),
            "coverage": coverage_report(cases),
        },
        "cases": records,
        "summary": summarize(records),
        "gate": None,
    }
    if apply_gate:
        report["gate"] = evaluate_gates(report, gates).as_dict()
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--suite", default="all", choices=["shopping", "merchant", "all"])
    parser.add_argument("--set", dest="case_set", default="ci", choices=["ci", "full"])
    parser.add_argument("--mode", default="replay", choices=["replay", "live"])
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument(
        "--model", default=None, help="override the agent model (default: the deployment config's)"
    )
    parser.add_argument(
        "--judge-model", default=None, help="override the judge model pinned in gates.yaml"
    )
    parser.add_argument("--report", type=Path, default=Path("evals-report.json"))
    parser.add_argument(
        "--case", action="append", default=[], help="run only this case id (repeatable)"
    )
    parser.add_argument(
        "--tag", action="append", default=[], help="run only cases with this tag (repeatable)"
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--gate",
        dest="gate",
        action="store_true",
        default=None,
        help="apply gates.yaml (default for --set ci)",
    )
    parser.add_argument("--no-gate", dest="gate", action="store_false")
    parser.add_argument("--list", action="store_true", help="list the selected cases and exit")
    parser.add_argument(
        "--verbose", action="store_true", help="print each trial's outcome to stderr"
    )
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.WARNING),
        format="%(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("evals").setLevel(logging.INFO)

    ids = set(args.case) or None
    tags = set(args.tag) or None
    if args.list:
        cases = load_cases(args.suite, args.case_set, ids=ids, tags=tags)
        for case in cases:
            flag = f" [skip: {case.skip}]" if case.skip else ""
            print(f"{case.id:60s} {case.set:4s} {','.join(case.tags)}{flag}")
        print(json.dumps(coverage_report(cases), indent=2))
        return 0
    started = time.monotonic()
    try:
        report = asyncio.run(
            run_suite(
                suite=args.suite,
                case_set=args.case_set,
                mode=args.mode,
                trials=args.trials,
                model=args.model,
                report_path=args.report,
                gate=args.gate,
                ids=ids,
                tags=tags,
                concurrency=args.concurrency,
                judge_model=args.judge_model,
                verbose=args.verbose,
            )
        )
    except BackendImportError as error:
        print(f"backend import failed: {error}", file=sys.stderr)
        return 3
    print_table(report)
    print(f"report: {args.report}  ({time.monotonic() - started:.0f}s)")
    if report["summary"]["setup_errors"]:
        print(f"setup errors: {', '.join(report['summary']['setup_errors'])}", file=sys.stderr)
    if report.get("gate") is not None and not report["gate"]["passed"]:
        return 1
    if report["summary"]["setup_errors"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
