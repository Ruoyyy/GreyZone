"""Shared Letta benchmark objective, independent of red-team framework."""
import json
import re
import argparse
from pathlib import Path

SCENARIO_ROOT = Path(__file__).resolve().parent.parent / "benchmark/scenarios"
DEFAULT_SCENARIO = "runtime-credential-disclosure"


def scenario_path(name):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise ValueError("Scenario must be a name under benchmark/scenarios")
    return SCENARIO_ROOT / name / "scenario.json"


def load_scenario(path=None):
    if path is None:
        path = scenario_path(DEFAULT_SCENARIO)
    path = Path(path).resolve()
    scenario = json.loads(path.read_text())
    if (scenario.get("schemaVersion") != 1 or scenario.get("environment") != "letta-issue"
            or scenario.get("id") != path.parent.name):
        raise ValueError("Unsupported or mismatched Letta benchmark scenario")
    turns = scenario.get("maxTurns")
    if type(turns) is not int or not 2 <= turns <= 20:
        raise ValueError("Letta scenario maxTurns must be between 2 and 20")
    criteria = scenario.get("successCriteria")
    if not isinstance(criteria, str) or not criteria.strip():
        raise ValueError("Letta scenario successCriteria is required")
    if not isinstance(scenario.get("name"), str) or not scenario["name"].strip():
        raise ValueError("Letta scenario name is required")
    if not isinstance(scenario.get("policy"), str) or not scenario["policy"].strip():
        raise ValueError("Letta scenario policy is required")
    monitors = scenario.get("oracleMonitors")
    if not isinstance(monitors, list) or not monitors or any(name not in {"exfil", "mem", "net"} for name in monitors):
        raise ValueError("Invalid Letta scenario Oracle monitors")
    objective_file = scenario.get("objectiveFile")
    if objective_file != "objective.md":
        raise ValueError("Letta scenario objectiveFile must be objective.md")
    objective = (path.parent / objective_file).read_text().strip()
    if not objective:
        raise ValueError("Objective file is empty")
    return scenario, objective


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Print a selected Letta benchmark objective")
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO)
    args = parser.parse_args()
    scenario, objective = load_scenario(scenario_path(args.scenario))
    print(objective)
    print(f"\nSuccess condition: {scenario['successCriteria']}")
    print(f"\nSend at most {scenario['maxTurns']} attack messages, including the issue body.")
