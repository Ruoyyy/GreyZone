"""Run one bounded PyRIT attack against one Clawith Oracle episode."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from redteam.dreadnode.run import env_value, run_command, scenario_data  # noqa: E402

OPENAI_DEFAULT_BASE = "https://api.openai.com/v1"
PYRIT_VERSION = "1.1.0"


def attacker_settings(model: str) -> tuple[str, str, str]:
    """Resolve model credentials before touching Oracle state."""
    endpoint = os.environ.get("OPENAI_BASE_URL") or env_value("OPENAI_BASE_URL") or OPENAI_DEFAULT_BASE
    key = env_value("OPENAI_API_KEY")
    if not key:
        raise ValueError("set OPENAI_API_KEY in the shell or Clawith/.env")
    if not endpoint.startswith("https://"):
        raise ValueError("OPENAI_BASE_URL must be an HTTPS OpenAI-compatible base URL")
    if not model.strip():
        raise ValueError("attacker model is empty")
    return endpoint, key, model


def check_version() -> None:
    try:
        version = importlib.metadata.version("pyrit")
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError("PyRIT is not installed; see redteam/pyrit/README.md") from exc
    if version != PYRIT_VERSION:
        raise RuntimeError(f"PyRIT {PYRIT_VERSION} is required; found {version}")


async def execute(args: argparse.Namespace) -> int:
    scenario, objective = scenario_data(args.scenario)
    if not objective or not scenario.get("attackerTurn"):
        raise ValueError("scenario objective and attackerTurn must be nonempty")
    if (ROOT / ".run/oracle/episode.json").exists():
        raise RuntimeError("Oracle episode already exists; reset before a new PyRIT run")
    bridge_key = env_value("MCP_API_KEY")
    if not bridge_key:
        raise ValueError("set a fixed MCP_API_KEY in Clawith/.env or the shell")
    endpoint, attacker_key, model = attacker_settings(args.model)
    check_version()

    from pyrit.executor.attack import AttackAdversarialConfig, AttackScoringConfig, RedTeamingAttack
    from pyrit.memory import CentralMemory
    from pyrit.models import Message
    from pyrit.prompt_target import OpenAIChatTarget
    from pyrit.setup import IN_MEMORY, initialize_pyrit_async
    from redteam.pyrit.adapter import ClawithTarget, OracleScorer

    await initialize_pyrit_async(memory_db_type=IN_MEMORY, initializers=[],
                                 load_defaults=False, env_files=[], silent=True)
    adversarial = OpenAIChatTarget(endpoint=endpoint, api_key=attacker_key,
                                   model_name=model)
    env = {**os.environ, "MCP_API_KEY": bridge_key,
           "ORACLE_ENABLE": scenario["oracleSuccessSurfaces"][0]}
    run_dir = ROOT / ".run/pyrit" / f"{args.scenario}-{uuid.uuid4()}"
    run_dir.mkdir(parents=True)
    python = sys.executable
    run_command([python, "-m", "oracle.run_episode", "--prepare"], env)
    run_command([python, "-m", "oracle.run_episode", "--arm"], env)
    episode = json.loads((ROOT / ".run/oracle/episode.json").read_text())
    state_file = run_dir / "attack-state.json"
    state_file.write_text(json.dumps({"episode": episode["n"], "turns": 0,
                                      "stopped": False}))
    attack_result = None
    run_error = None
    try:
        os.environ.update({
            "MCP_API_KEY": bridge_key,
            "CLAWITH_EPISODE": str(episode["n"]),
            "CLAWITH_SURFACE": scenario["oracleSuccessSurfaces"][0],
            "CLAWITH_MAX_TURNS": str(args.max_turns),
            "CLAWITH_STATE_FILE": str(state_file),
        })
        from redteam.dreadnode.attacker_mcp import perform_turn

        target = ClawithTarget(turn=perform_turn, episode=episode["n"],
                               surface=scenario["oracleSuccessSurfaces"][0])
        scorer = OracleScorer(episode=episode["n"],
                              surface=scenario["oracleSuccessSurfaces"][0])
        attack = RedTeamingAttack(
            objective_target=target,
            attack_adversarial_config=AttackAdversarialConfig(target=adversarial),
            attack_scoring_config=AttackScoringConfig(objective_scorer=scorer),
            max_turns=args.max_turns,
        )
        attack_result = await attack.execute_async(
            objective=f"{objective}\n\nPolicy to test: {scenario['policy']}",
            next_message=Message.from_prompt(prompt=scenario["attackerTurn"], role="user"),
            memory_labels={"scenario": args.scenario, "oracle_episode": str(episode["n"])},
        )
    except Exception as exc:
        run_error = f"{type(exc).__name__}: {exc}"
    finally:
        run_command([python, "-m", "oracle.watch", "--close"], env)

    if attack_result is not None and attack_result.outcome.value in {"error", "undetermined"}:
        run_error = run_error or (attack_result.error_message or
                                  f"PyRIT outcome: {attack_result.outcome.value}")

    oracle = json.loads((ROOT / ".run/oracle" / f"result-{episode['n']}.json").read_text())
    (run_dir / "oracle-result.json").write_text(json.dumps(oracle, indent=2) + "\n")
    state = json.loads(state_file.read_text())
    summary = {"scenario": args.scenario, "episode": episode["n"],
               "turns": state["turns"], "oracleVerdict": oracle["verdict"],
               "runStatus": "error" if run_error else
                            ("invalid-zero-turn" if state["turns"] == 0 else "completed"),
               "error": run_error}
    if attack_result is not None:
        summary["pyritOutcome"] = attack_result.outcome.value
        (run_dir / "pyrit-result.json").write_text(
            json.dumps(attack_result.model_dump(mode="json"), indent=2) + "\n")
        messages = CentralMemory.get_memory_instance().get_conversation_messages(
            conversation_id=attack_result.conversation_id)
        (run_dir / "transcript.json").write_text(json.dumps(
            [message.model_dump(mode="json") for message in messages], indent=2) + "\n")
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"[pyrit] result: {run_dir / 'summary.json'}")
    print(f"[pyrit] Oracle: {oracle['verdict']}; turns: {state['turns']}; run: {summary['runStatus']}")
    if run_error:
        print(f"[pyrit] {run_error}", file=sys.stderr)
    return 1 if run_error or state["turns"] == 0 else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="kb-poison")
    parser.add_argument("--model", default=env_value("OPENAI_MODEL"))
    parser.add_argument("--max-turns", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.max_turns <= 20:
        parser.error("max-turns must be 1..20")
    return asyncio.run(execute(args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[pyrit] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
