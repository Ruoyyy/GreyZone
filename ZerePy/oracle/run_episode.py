"""Episode driver (sibling-style). The victim is launched only AFTER the attacker
has planted, so it reads the complete planted state on its first cycle:

  1. --prepare --scenario <id>  resolve the boundary, verify the substrate,
                                write the episode persona (victim NOT yet running)
  2. (plant window)             the red-team agent acts as ruoyu via the RedTeam MCP
  3. --start                    take the baseline, THEN launch the victim
  4. watch.py                   observe-only; close on idle and judge
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from oracle import common as C
from oracle import truth as T

VICTIM_CONTAINER = "zerepy-victim"
RESOLVED_PERSONA = "_victim"  # agents/_victim.json, mounted live into the container


def load_scenario(path: str) -> dict:
    sc = json.loads((Path(path) / "scenario.json").read_text())
    sc["_dir"] = path
    return sc


def _resolve_persona(scenario: dict, scope: dict) -> str:
    """Write agents/_victim.json from the scenario's persona. Personas are
    criterion-based (they state a rule, not a hardcoded party), so there is
    nothing env-specific to substitute — this is a straight copy."""
    src = Path(C.REPO_ROOT) / scenario["victim"]["persona"]
    (Path(C.REPO_ROOT) / "agents" / f"{RESOLVED_PERSONA}.json").write_text(src.read_text())
    return RESOLVED_PERSONA


def _launch_victim(persona: str) -> None:
    subprocess.run(["docker", "rm", "-f", VICTIM_CONTAINER],
                   capture_output=True, cwd=C.REPO_ROOT)
    subprocess.run(
        ["docker", "compose", "run", "-d", "--name", VICTIM_CONTAINER, "zerepy",
         "python", "-c",
         f"from src.agent import ZerePyAgent; a = ZerePyAgent('{persona}'); a._setup_llm_provider(); a.loop()"],
        check=True, cwd=C.REPO_ROOT)


def prepare(scenario: dict) -> None:
    scope = T.resolve_scope(scenario)
    # substrate checks per task
    if scenario["task"] == "airdrop":
        alice = C.user_address("alice").lower()
        ruoyu = C.user_address("ruoyu").lower()
        assert alice in scope["eligible"], "alice is not eligible — reseed the chain"
        assert ruoyu not in scope["eligible"], "ruoyu is eligible — attack is trivial, reseed"
        print(f"[prepare] substrate ok: |E|={len(scope['eligible'])}, alice in E, ruoyu not")
    persona = _resolve_persona(scenario, scope)
    C.save_episode({
        "state": "prepared", "scenario": scenario["id"], "scenario_dir": scenario["_dir"],
        "task": scenario["task"], "surfaces": scenario["oracleSuccessSurfaces"],
        "scope": scope, "persona": persona,
    })
    print(f"[prepare] ready ({scenario['task']}); plant as ruoyu, then run --start to baseline + launch the victim")


def start() -> None:
    ep = C.load_episode()
    if not ep or ep["state"] not in ("prepared", "running"):
        sys.exit("[start] no prepared episode — run --prepare first")
    ep["baseline"] = {
        "block": C.block_height(),
        "post_cursor": C.max_post_id(),
        "admin": ep["scope"]["admin"],
    }
    ep["n"] = C.next_episode_number()
    ep["state"] = "running"
    C.save_episode(ep)
    C.alerts_path(ep["n"]).parent.mkdir(parents=True, exist_ok=True)
    _launch_victim(ep["persona"])  # baseline captured first, so the victim's reactions are post-baseline
    print(f"[start] episode {ep['n']} running (baseline block {ep['baseline']['block']}); "
          f"victim launched — run watch.py to close on idle and judge")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--prepare", action="store_true")
    ap.add_argument("--start", action="store_true")
    ap.add_argument("--scenario")
    args = ap.parse_args()
    if args.prepare:
        if not args.scenario:
            sys.exit("--prepare needs --scenario")
        prepare(load_scenario(args.scenario))
    elif args.start:
        start()
    else:
        ap.print_help()
