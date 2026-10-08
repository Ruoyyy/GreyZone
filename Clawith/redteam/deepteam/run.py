"""Run one DeepTeam linear attack against one armed Clawith Oracle episode."""

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

ZHIPU_BASE = "https://open.bigmodel.cn/api/paas/v4"
DEEPTEAM_VERSION = "1.0.9"


def attacker_settings(model: str) -> tuple[str, str, str]:
    """Resolve attacker credentials without sending a key to another endpoint."""
    endpoint = os.environ.get("DEEPTEAM_ATTACKER_BASE_URL") or ZHIPU_BASE
    key = os.environ.get("DEEPTEAM_ATTACKER_API_KEY")
    if not key and endpoint == ZHIPU_BASE:
        key = env_value("ZHIPU_API_KEY")
    if not key:
        raise ValueError("set DEEPTEAM_ATTACKER_API_KEY, or ZHIPU_API_KEY for the default endpoint")
    if not endpoint.startswith("https://"):
        raise ValueError("DEEPTEAM_ATTACKER_BASE_URL must be an HTTPS OpenAI-compatible base URL")
    if not model.strip():
        raise ValueError("attacker model is empty")
    return endpoint, key, model


def check_version() -> None:
    try:
        installed = importlib.metadata.version("deepteam")
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError("DeepTeam is not installed; see redteam/deepteam/README.md") from exc
    if installed != DEEPTEAM_VERSION:
        raise RuntimeError(f"DeepTeam {DEEPTEAM_VERSION} is required; found {installed}")


async def execute(args: argparse.Namespace) -> int:
    scenario, objective = scenario_data(args.scenario)
    if not objective or not scenario.get("attackerTurn"):
        raise ValueError("scenario objective and attackerTurn must be nonempty")
    if (ROOT / ".run/oracle/episode.json").exists():
        raise RuntimeError("Oracle episode already exists; reset before a new DeepTeam run")
    bridge_key = env_value("MCP_API_KEY")
    if not bridge_key:
        raise ValueError("set a fixed MCP_API_KEY in Clawith/.env or the shell")
    endpoint, attacker_key, model_name = attacker_settings(args.model)
    check_version()

    os.environ["DEEPTEAM_TELEMETRY_OPT_OUT"] = "YES"
    os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "1"
    from deepeval.models import OpenAIModel
    from deepteam.attacks.multi_turn import LinearJailbreaking
    from deepteam.attacks.multi_turn.linear_jailbreaking.schema import Feedback
    from deepteam.test_case import RTTurn
    from redteam.deepteam.adapter import ClawithConversation

    simulator = OpenAIModel(model=model_name, base_url=endpoint, api_key=attacker_key)
    preflight, _ = await simulator.a_generate(
        "Return only JSON: {\"jailbroken\": false, \"suggestion\": \"ready\"}.",
        schema=Feedback,
    )
    if not isinstance(preflight, Feedback):
        raise RuntimeError("attacker model did not return DeepTeam's structured response")

    env = {**os.environ, "MCP_API_KEY": bridge_key,
           "ORACLE_ENABLE": scenario["oracleSuccessSurfaces"][0]}
    run_dir = ROOT / ".run/deepteam" / f"{args.scenario}-{uuid.uuid4()}"
    run_dir.mkdir(parents=True)
    python = sys.executable
    run_command([python, "-m", "oracle.run_episode", "--prepare"], env)
    run_command([python, "-m", "oracle.run_episode", "--arm"], env)
    episode = json.loads((ROOT / ".run/oracle/episode.json").read_text())
    state_file = run_dir / "attack-state.json"
    state_file.write_text(json.dumps({"episode": episode["n"], "turns": 0,
                                      "stopped": False}))

    conversation = None
    deepteam_turns = None
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

        conversation = ClawithConversation(
            turn=perform_turn, episode=episode["n"],
            surface=scenario["oracleSuccessSurfaces"][0])
        attack = LinearJailbreaking(num_turns=args.max_turns - 1,
                                    simulator_model=simulator)
        deepteam_turns = await attack._a_get_turns(
            model_callback=conversation.callback,
            turns=[RTTurn(role="user", content=scenario["attackerTurn"])],
            vulnerability=objective,
            vulnerability_type=scenario["oracleSuccessSurfaces"][0],
            metric_check=conversation.metric_check,
        )
    except Exception as exc:
        run_error = f"{type(exc).__name__}: {exc}"
    finally:
        run_command([python, "-m", "oracle.watch", "--close"], env)

    oracle = json.loads((ROOT / ".run/oracle" / f"result-{episode['n']}.json").read_text())
    (run_dir / "oracle-result.json").write_text(json.dumps(oracle, indent=2) + "\n")
    state = json.loads(state_file.read_text())
    events = conversation.events if conversation else []
    (run_dir / "turns.json").write_text(json.dumps(events, indent=2, ensure_ascii=False) + "\n")
    if deepteam_turns is not None:
        (run_dir / "deepteam-transcript.json").write_text(json.dumps(
            [turn.model_dump(mode="json") for turn in deepteam_turns],
            indent=2, ensure_ascii=False) + "\n")
    summary = {"scenario": args.scenario, "episode": episode["n"],
               "turns": state["turns"], "oracleVerdict": oracle["verdict"],
               "runStatus": "error" if run_error else
                            ("invalid-zero-turn" if state["turns"] == 0 else "completed"),
               "error": run_error}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"[deepteam] result: {run_dir / 'summary.json'}")
    print(f"[deepteam] Oracle: {oracle['verdict']}; turns: {state['turns']}; run: {summary['runStatus']}")
    if run_error:
        print(f"[deepteam] {run_error}", file=sys.stderr)
    return 1 if run_error or state["turns"] == 0 else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="kb-poison")
    parser.add_argument("--model", default=os.environ.get("DEEPTEAM_ATTACKER_MODEL", "glm-4.7"))
    parser.add_argument("--max-turns", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.max_turns <= 20:
        parser.error("max-turns must be 1..20")
    return asyncio.run(execute(args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[deepteam] {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
