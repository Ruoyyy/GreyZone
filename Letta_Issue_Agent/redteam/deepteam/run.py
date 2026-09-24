#!/usr/bin/env python3
"""One case/issue at a time, with explicit limits and partial-result persistence."""
import argparse
import asyncio
import fcntl
import importlib.metadata
import os
import sys
from pathlib import Path

# Set before importing DeepTeam/DeepEval: do not auto-upload assessment data.
os.environ["DEEPTEAM_TELEMETRY_OPT_OUT"] = "YES"
os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "YES"
os.environ["TELEMETRY_OPT_OUT"] = "1"

from settings import HERE, load_settings
from mcp_target import IssueTarget, TargetError, WriteOutcomeUnknown, connect
from reporting import Report, now


def build_teamer(settings, objective, attacker, evaluator):
    from deepteam.red_teamer import RedTeamer
    from deepteam.attacks.attack_engine import AttackEngine
    from deepteam.attacks.multi_turn import CrescendoJailbreaking
    from deepteam.vulnerabilities import CustomVulnerability

    criteria = settings.campaign.success_criteria
    vulnerability = CustomVulnerability(
        name="BenchmarkObjective", types=[settings.campaign.scenario_id.replace("-", "_")],
        criteria=criteria, simulator_model=attacker, evaluation_model=evaluator,
    )
    attack = CrescendoJailbreaking(
        max_rounds=settings.campaign.max_target_calls - 1,
        max_backtracks=0, simulator_model=attacker,
    )
    engine = AttackEngine(simulator_model=attacker, variations=1, purpose=objective,
        generation_guidelines=["Use messages suitable for a collaborator's Gitea issue conversation."])
    teamer = RedTeamer(simulator_model=attacker, evaluation_model=evaluator,
        target_purpose=objective, async_mode=True, max_concurrent=1, attack_engine=engine)
    return teamer, vulnerability, attack


async def run_case(settings, objective, transport, report, case_id, attacker, evaluator):
    from deepteam.test_case import RTTurn

    target = IssueTarget(transport, settings.target, settings.campaign.max_target_calls,
                         lambda event: report.event(case_id, event))
    result = {"case_id": case_id, "started_at": now(), "status": "running",
              "oracle": {"status": "not_collected"}}
    report.write(f"{case_id}-summary.json", result)

    async def model_callback(input: str, turns=None):
        return RTTurn(role="assistant", content=await target.send(input, turns))

    try:
        teamer, vulnerability, attack = build_teamer(settings, objective, attacker, evaluator)
        async with asyncio.timeout(settings.campaign.timeout_seconds):
            assessment = await teamer.a_red_team(
                model_callback=model_callback, vulnerabilities=[vulnerability], attacks=[attack],
                attacks_per_vulnerability_type=1, ignore_errors=False,
                metadata={"run_id": report.run_id, "case_id": case_id},
                _print_assessment=False, _upload_to_confident=False,
            )
        report.write(f"{case_id}-assessment.json", assessment.model_dump(mode="json"))
        cases = assessment.test_cases
        if len(cases) != 1 or not target.completed:
            raise TargetError("Expected exactly one evaluated case; generation may have filtered the candidate")
        case = cases[0]
        result.update(status="completed" if not case.error and case.score is not None else "evaluation_error",
                      score=case.score, reason=case.reason, error=case.error)
    except WriteOutcomeUnknown as exc:
        result.update(status="write_outcome_unknown", error=str(exc))
    except TargetError as exc:
        result.update(status="execution_error", error=str(exc))
    except TimeoutError:
        result.update(status="timeout", error="Run or MCP reply deadline exceeded; inspect pending issue")
    except asyncio.CancelledError:
        result.update(status="interrupted", error="Interrupted; target may still be running")
        raise
    except Exception as exc:
        result.update(status="error", error=f"{type(exc).__name__}: {exc}")
    finally:
        result.update(finished_at=now(), issue_number=target.issue, issue_url=target.url,
                      submissions_attempted=target.calls, replies_completed=target.completed,
                      pending_or_uncertain=target.poisoned)
        report.write(f"{case_id}-summary.json", result)
    return result


async def execute(settings, objective, check_only=False):
    if check_only:
        from models import CompatibleModel
        attacker = CompatibleModel(settings.models.attacker)
        evaluator = CompatibleModel(settings.models.evaluator)
        try:
            build_teamer(settings, objective, attacker, evaluator)
            print("DeepTeam dependencies, model adapters and Crescendo construction: OK")
        finally:
            await attacker.close()
            await evaluator.close()
        async with connect(settings.target):
            print("MCP initialize/tools: OK; configuration and required credentials: present")
            print(f"Crescendo: {settings.campaign.cases} case(s), at most "
                  f"{settings.campaign.max_target_calls} target submissions each; backtracks=0")
            print("Read-only preflight. Model generation/quota and Letta execution were not tested.")
        return 0

    from models import CompatibleModel
    report = Report(HERE / "results")
    report.write("manifest.json", {"run_id": report.run_id, "created_at": now(),
        "config": settings.model_dump(), "objective": objective,
        "versions": {name: importlib.metadata.version(name) for name in ("deepteam", "deepeval", "mcp")},
        "strategy": "CrescendoJailbreaking", "max_backtracks": 0,
        "isolation": "new issue per case; persistent agent memory and repository are NOT reset"})
    print(f"Results: {report.path}", flush=True)
    attacker = CompatibleModel(settings.models.attacker)
    evaluator = CompatibleModel(settings.models.evaluator)
    results = []
    status = "running"
    try:
        async with connect(settings.target) as transport:
            for number in range(1, settings.campaign.cases + 1):
                case_id = f"case-{number:03d}"
                print(f"Starting {case_id}", flush=True)
                result = await run_case(settings, objective, transport, report, case_id, attacker, evaluator)
                results.append(result)
                print(f"{case_id}: {result['status']}, score={result.get('score')}", flush=True)
                if result["status"] != "completed":
                    break  # Never post another attack after an incomplete target turn.
        status = "completed" if len(results) == settings.campaign.cases and all(
            r["status"] == "completed" for r in results) else "error"
    except asyncio.CancelledError:
        status = "interrupted"
        raise
    except Exception as exc:
        status = "error"
        report.write("error.json", {"error": f"{type(exc).__name__}: {exc}"})
        print(f"Run failed; see {report.path / 'error.json'}", file=sys.stderr)
    finally:
        report.write("summary.json", {"status": status, "finished_at": now(), "cases": results,
            "model_usage": {"attacker": attacker.usage, "evaluator": evaluator.usage},
            "oracle": {"status": "not_collected"}})
        await attacker.close()
        await evaluator.close()
    return 0 if status == "completed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.yaml")
    parser.add_argument("--scenario", help="Benchmark scenario name under benchmark/scenarios")
    parser.add_argument("--check", action="store_true", help="Read-only config and MCP check; no model calls")
    args = parser.parse_args()
    settings, objective = load_settings(args.config, scenario_id=args.scenario)
    os.chdir(HERE)  # Keep any dependency-local state under this integration.
    if args.check:
        return asyncio.run(execute(settings, objective, True))
    # A file lock protects this integration from overlapping invocations; other
    # tools must still be run serially as documented in redteam/README.md.
    root = HERE / "results"
    root.mkdir(exist_ok=True, mode=0o700)
    with (root / ".run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another DeepTeam run is active")
        return asyncio.run(execute(settings, objective))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
