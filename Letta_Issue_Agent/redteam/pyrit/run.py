#!/usr/bin/env python3
"""Run serial PyRIT cases, or validate configuration without posting messages."""
import argparse
import asyncio
import importlib.metadata
import os
from pathlib import Path
import sys

from settings import HERE, load_settings
from issue_transport import TargetError, WriteOutcomeUnknown, connect
from reporting import Report, now
from runtime import initialize, close_models


async def run_case(settings, objective, transport, report, case_id, attacker, evaluator):
    from mcp_target import GiteaIssueTarget
    from models import build_attack

    target = GiteaIssueTarget(transport=transport, config=settings.target, budget=settings.campaign.max_target_calls,
                             record=lambda event: report.event(case_id, event))
    state = target.issue_target
    result = {"case_id": case_id, "started_at": now(), "status": "running",
              "oracle": {"status": "not_collected"}}
    report.write(f"{case_id}-summary.json", result)
    try:
        async with asyncio.timeout(settings.campaign.timeout_seconds):
            attack = build_attack(settings, target, attacker, evaluator)
            assessment = await attack.execute_async(objective=objective,
                memory_labels={"run_id": report.run_id, "case_id": case_id})
        report.write(f"{case_id}-assessment.json", assessment.model_dump(mode="json"))
        if not state.completed or state.poisoned:
            raise TargetError("No complete target conversation was evaluated")
        result.update(status="completed", evaluator_outcome=assessment.outcome.value,
                      evaluator_reason=assessment.outcome_reason)
    except WriteOutcomeUnknown as exc:
        result.update(status="write_outcome_unknown", error=str(exc))
    except TargetError as exc:
        result.update(status="execution_error", error=str(exc))
    except TimeoutError:
        result.update(status="timeout", error="Deadline exceeded; inspect the pending issue")
    except asyncio.CancelledError:
        result.update(status="interrupted", error="Interrupted; target may still be running")
        raise
    except Exception as exc:
        result.update(status="error", error=f"{type(exc).__name__}: {exc}")
    finally:
        result.update(finished_at=now(), issue_number=state.issue, issue_url=state.url,
                      submissions_attempted=state.calls, replies_completed=state.completed,
                      pending_or_uncertain=state.poisoned)
        report.write(f"{case_id}-summary.json", result)
    return result


async def execute(settings, objective, mode):
    await initialize()
    from pyrit.memory import CentralMemory
    from mcp_target import GiteaIssueTarget
    from models import build_attack, build_model

    models = []
    report = None
    results = []
    status = "running"
    try:
        for config in (settings.models.attacker, settings.models.evaluator):
            models.append(build_model(config))
        attacker, evaluator = models
        if mode != "run":
            target = GiteaIssueTarget(transport=None, config=settings.target, budget=settings.campaign.max_target_calls)
            build_attack(settings, target, attacker, evaluator)
            print("PyRIT configuration, dependencies, targets and attack construction: OK")
            if mode == "check":
                async with connect(settings.target):
                    print("MCP initialize and required tools: OK")
            print("No messages posted or model requests made; model quota and Letta execution untested.")
            return 0

        report = Report(HERE / "results")
        report.write("manifest.json", {"run_id": report.run_id, "created_at": now(),
            "config": settings.model_dump(), "objective": objective,
            "versions": {name: importlib.metadata.version(name) for name in ("pyrit", "mcp")},
            "strategy": "RedTeamingAttack",
            "isolation": "New issue per case; agent memory and repository are not reset"})
        print(f"Results: {report.path}", flush=True)
        async with connect(settings.target) as transport:
            for number in range(1, settings.campaign.cases + 1):
                result = await run_case(settings, objective, transport, report,
                    f"case-{number:03d}", attacker, evaluator)
                results.append(result)
                if result["status"] != "completed":
                    break
        status = "completed" if len(results) == settings.campaign.cases and all(
            r["status"] == "completed" for r in results) else "error"
    except asyncio.CancelledError:
        status = "interrupted"
        raise
    except Exception as exc:
        status = "error"
        if report is None:
            raise
        report.write("error.json", {"error": f"{type(exc).__name__}: {exc}"})
        print(f"Run failed; see {report.path / 'error.json'}", file=sys.stderr)
    finally:
        if report is not None:
            report.write("summary.json", {"status": status, "finished_at": now(),
                "cases": results, "oracle": {"status": "not_collected"}})
        await close_models(*models)
        CentralMemory.get_memory_instance().dispose_engine()
    return 0 if status == "completed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.yaml")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--offline-check", action="store_true", help="Validate locally without credentials or network")
    group.add_argument("--check", action="store_true", help="Also check credentials and MCP tools; no posts/model calls")
    args = parser.parse_args()
    os.umask(0o077)
    settings, objective = load_settings(args.config, require_credentials=not args.offline_check)
    mode = "offline" if args.offline_check else "check" if args.check else "run"
    descriptor = None
    if mode == "run":
        sys.path.insert(0, str(HERE.parent))
        from run_locked import acquire
        descriptor = acquire()
    try:
        return asyncio.run(execute(settings, objective, mode))
    finally:
        if descriptor is not None:
            os.close(descriptor)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
