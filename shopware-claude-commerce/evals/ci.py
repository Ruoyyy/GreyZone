# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The CI gate: which cases the CI set holds and the thresholds a report must clear.

    python -m evals.ci --report out.json            # gate an existing report
    python -m evals.ci --run --suite all --trials 2  # run the CI set, then gate it

The CI set is every case with ``set: ci``; ``select_ci_cases`` also asserts the policy
that every ``safety`` case is in it (the schema test enforces the same). Thresholds come
from ``evals/gates.yaml``: pass rate per tag, cache-hit rate from the second trial on,
estimated cost per turn per suite, and a ceiling on judge failures.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import GATES_PATH
from .cases import TAGS, Case, load_cases


class GatePolicyError(ValueError):
    pass


def load_gates(path: Path = GATES_PATH) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise GatePolicyError(f"{path}: expected a mapping")
    for key in ("pass_rate", "cache", "cost", "pricing"):
        if key not in data:
            raise GatePolicyError(f"{path}: missing section {key!r}")
    return data


def rates_for(model: str, gates: dict[str, Any]) -> dict[str, float]:
    """The longest model-prefix match in ``pricing.models``, else ``pricing.default``."""
    pricing = gates.get("pricing") or {}
    best: tuple[int, dict[str, float]] | None = None
    for prefix, rates in (pricing.get("models") or {}).items():
        if model.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), rates)
    return dict(best[1] if best else pricing.get("default") or {})


def select_ci_cases(cases: list[Case]) -> list[Case]:
    """The CI set: every ``set: ci`` case. Every safety case must be in it."""
    outside = [case.id for case in cases if "safety" in case.tags and case.set != "ci"]
    if outside:
        raise GatePolicyError(f"safety cases must be in the ci set: {', '.join(outside)}")
    return [case for case in cases if case.set == "ci"]


@dataclass
class GateResult:
    passed: bool
    failures: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "failures": self.failures, "metrics": self.metrics}


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def evaluate_gates(report: dict[str, Any], gates: dict[str, Any]) -> GateResult:
    """Apply the policy to a runner report (``evals.runner`` JSON shape)."""
    cases = [c for c in report.get("cases") or [] if not c.get("skipped")]
    failures: list[str] = []
    metrics: dict[str, Any] = {}

    # Pass rate per tag over every trial of every case carrying the tag.
    thresholds = gates.get("pass_rate") or {}
    for tag in TAGS:
        trials = [t for c in cases if tag in (c.get("tags") or []) for t in c.get("trials") or []]
        if not trials:
            continue
        rate = sum(1 for t in trials if t.get("passed")) / len(trials)
        metrics[f"pass_rate.{tag}"] = round(rate, 4)
        floor = thresholds.get(tag)
        if floor is not None and rate < float(floor):
            failing = sorted(
                {
                    c["id"]
                    for c in cases
                    if tag in c.get("tags", [])
                    for t in c.get("trials", [])
                    if not t.get("passed")
                }
            )
            failures.append(
                f"pass_rate.{tag} {rate:.2f} < {float(floor):.2f} (failing: {', '.join(failing)})"
            )

    # Cache-hit rate from the second trial on.
    cache = gates.get("cache") or {}
    from_trial = int(cache.get("from_trial", 2))
    hits = [
        float(t["cache_hit_rate"])
        for c in cases
        for t in c.get("trials") or []
        if int(t.get("trial", 0)) >= from_trial and t.get("cache_hit_rate") is not None
    ]
    if hits:
        metrics["cache_hit_rate"] = _mean(hits)
        floor = float(cache.get("min_hit_rate", 0))
        if metrics["cache_hit_rate"] < floor:
            failures.append(
                f"cache_hit_rate {metrics['cache_hit_rate']:.2f} < {floor:.2f} (trials >= {from_trial})"
            )
    else:
        metrics["cache_hit_rate"] = None

    # Cost per turn per suite.
    budgets = (gates.get("cost") or {}).get("max_usd_per_turn") or {}
    for suite in ("shopping", "merchant"):
        costs = [
            float(t["cost_usd"])
            for c in cases
            if c.get("suite") == suite
            for t in c.get("trials") or []
            if t.get("cost_usd") is not None
        ]
        if not costs:
            continue
        metrics[f"cost_usd_per_turn.{suite}"] = _mean(costs)
        budget = budgets.get(suite)
        if budget is not None and metrics[f"cost_usd_per_turn.{suite}"] > float(budget):
            failures.append(
                f"cost_usd_per_turn.{suite} {metrics[f'cost_usd_per_turn.{suite}']:.4f} > {float(budget):.4f}"
            )

    # Judge failures.
    judge_scores = [
        s
        for c in cases
        for t in c.get("trials") or []
        for s in t.get("scores") or []
        if s.get("kind") == "judge"
    ]
    if judge_scores:
        share = sum(1 for s in judge_scores if s.get("judge_error")) / len(judge_scores)
        metrics["judge_error_share"] = round(share, 4)
        ceiling = float((gates.get("judge") or {}).get("max_error_share", 1.0))
        if share > ceiling:
            failures.append(f"judge_error_share {share:.2f} > {ceiling:.2f}")

    # Errors that are not the agent's: a setup error means the case could not run.
    setup_errors = sorted(
        {
            c["id"]
            for c in cases
            for t in c.get("trials") or []
            if t.get("error_kind") == "setup_error"
        }
    )
    if setup_errors:
        failures.append(f"setup errors: {', '.join(setup_errors)}")

    return GateResult(passed=not failures, failures=failures, metrics=metrics)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--report", type=Path, default=Path("evals-report.json"))
    parser.add_argument("--gates", type=Path, default=GATES_PATH)
    parser.add_argument(
        "--run", action="store_true", help="run the CI set first (needs ANTHROPIC_API_KEY)"
    )
    parser.add_argument("--suite", default="all", choices=["shopping", "merchant", "all"])
    parser.add_argument("--mode", default="replay", choices=["replay", "live"])
    parser.add_argument("--trials", type=int, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--list", action="store_true", help="print the CI set and exit")
    args = parser.parse_args(argv)

    gates = load_gates(args.gates)
    if args.list:
        for case in select_ci_cases(load_cases(args.suite)):
            print(f"{case.id:60s} {','.join(case.tags)}")
        return 0
    if args.run:
        from .runner import run_suite

        trials = args.trials or int(gates.get("trials", 2))
        report = asyncio.run(
            run_suite(
                suite=args.suite,
                case_set="ci",
                mode=args.mode,
                trials=trials,
                model=args.model,
                report_path=args.report,
                gates=gates,
                gate=False,
            )
        )
    else:
        if not args.report.exists():
            print(f"no report at {args.report}; run the suite first or pass --run", file=sys.stderr)
            return 2
        report = json.loads(args.report.read_text(encoding="utf-8"))
    result = evaluate_gates(report, gates)
    print(json.dumps(result.as_dict(), indent=2))
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
