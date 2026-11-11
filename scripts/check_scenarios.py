#!/usr/bin/env python3
"""Check repository scenario contracts without importing runtime code or starting services."""

import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Directory, scope field, native Oracle source, optional literal constant.
RANGES = {
    "Clawith": ("benchmark", "oracleSuccessSurfaces", "oracle/common.py", "SURFACES"),
    "Inbox_Zero_Mail_Agent": ("benchmark", "oracleSuccessSurfaces", "redteam/scenario.py", "MONITORS"),
    "Letta_Issue_Agent": ("benchmark", "oracleMonitors", None, None),
    "ZerePy": ("benchmark", "oracleSuccessSurfaces", "oracle/judge.py", None),
    "agent-openai-java-banking-assistant": ("benchmark", None, "oracle/common.py", "ALL_SURFACES"),
    "alpaca-mcp-server": ("benchmark", "oracleSurfaces", "sim/oracle/judge.py", "SURFACE_ORDER"),
    "commonly": ("benchmark", "oracleSuccessSurfaces", "oracle/judge.py", "ORACLE_SURFACES"),
    "kubectl-ai": ("benchmark", "oracleSurfaces", "oracle/judge.py", None),
    "shopware-claude-commerce": ("benchmark", "oracleSurfaces", "oracle/common.py", "ALL_SURFACES"),
    "smart-home": ("sim/benchmark", "oracleSurfaces", "sim/oracle/run_episode.py", None),
}


def literal_constant(path, name):
    for node in ast.parse(path.read_text()).body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            return ast.literal_eval(node.value)
    raise ValueError(f"Missing native constant {name} in {path}")


def native_surfaces(project, specification):
    _, _, source, constant = specification
    if source is None:
        return {path.stem for path in (project / "oracle").glob("*.py")} - {"common", "test_oracle"}
    path = project / source
    if constant:
        return set(literal_constant(path, constant)) - {"pipeline"}
    surfaces = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "surface" and isinstance(value, ast.Constant):
                    surfaces.add(value.value)
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id == "judgments" and isinstance(node.slice, ast.Constant)):
            surfaces.add(node.slice.value)
    if not surfaces:
        raise ValueError(f"No native Oracle surfaces found in {path}")
    return surfaces


def check_scenario(project, benchmark, path, field, allowed):
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("Scenario must be a JSON object")
    identifier = path.parent.name if path.name == "scenario.json" else path.stem
    if data.get("id", data.get("name")) != identifier:
        raise ValueError("Scenario identifier does not match its path")
    reference = data.get("objectiveFile")
    if not isinstance(reference, str) or not reference or Path(reference).is_absolute():
        raise ValueError("Missing objectiveFile")
    objective = (path.parent / reference).resolve()
    if not objective.is_relative_to(benchmark.resolve()) or not objective.read_text().strip():
        raise ValueError("Objective must be nonempty and inside the benchmark directory")
    for key in ("policy", "successCriteria"):
        if key in data and (not isinstance(data[key], str) or not data[key].strip()):
            raise ValueError(f"Empty {key}")
    if field:
        surfaces = data.get(field)
    else:
        plan = data.get("plan")
        if plan not in {"direct-multi-turn", "indirect-single-shot"}:
            raise ValueError("Unknown banking plan")
        surfaces = data.get("oracleSurfaces", ["attacker-payment"] if plan.startswith("indirect") else sorted(allowed))
    if (not isinstance(surfaces, list) or not surfaces or any(not isinstance(s, str) for s in surfaces)
            or len(set(surfaces)) != len(surfaces) or set(surfaces) - allowed):
        raise ValueError("Invalid or unsupported Oracle success scope")
    if project.name == "Inbox_Zero_Mail_Agent" and not set(surfaces) <= set(data.get("oracleMonitors", [])):
        raise ValueError("Success surface is not monitored")
    if project.name == "alpaca-mcp-server":
        tasks = literal_constant(project / "sim/oracle/tasks.py", "TASKS")
        task = data["victimTask"]
        if " ".join(task["text"].split()) != " ".join(tasks[task["key"]]["prompt"].split()):
            raise ValueError("Victim task differs from the native authorization template")
    persona = data.get("victim", {}).get("persona") if isinstance(data.get("victim"), dict) else None
    if persona and not (project / persona).is_file():
        raise ValueError("Missing victim persona")
    task = data.get("victimTask", data.get("question", data.get("attackerTurn", data.get("task", data.get("subject")))))
    if isinstance(task, dict):
        task = task.get("text")
    # Letta issues and direct banking conversations are generated by the external attacker.
    if not task and project.name not in {"Letta_Issue_Agent", "agent-openai-java-banking-assistant"}:
        raise ValueError("Missing task or attacker trigger")
    calibration = data.get("calibration", {}).get("attacks", [])
    if project.name == "shopware-claude-commerce":
        calibration = json.loads((project / "oracle/calibrations" / f"{identifier}.json").read_text())["attackFixed"]
        if any(call["tool"] not in data["attacker"]["channels"] for call in calibration):
            raise ValueError("Calibration uses an undeclared attacker channel")
    for call in calibration:
        if not call.get("tool") or not isinstance(call.get("arguments"), dict):
            raise ValueError("Invalid calibration tool call")
        image = call["arguments"].get("image_file")
        if image and not (path.parent / image).is_file():
            raise ValueError("Missing calibration image")
    if project.name == "smart-home":
        channels = {"camera_image": "set_camera_image_data", "entity_attributes": "set_entity_attributes",
                    "web_page": "set_web_page", "ssdp_device": "announce_device"}
        tools = {channels[channel] for channel in data["attacker"]["channels"]}
        if any(call["tool"] not in tools for call in calibration):
            raise ValueError("Calibration uses an undeclared attacker channel")
    return {"scenario": identifier, "objective": str(objective.relative_to(project.resolve())),
            "success_surfaces": surfaces, "status": "PASS"}


def audit(root=ROOT, projects=None):
    rows = []
    for name in projects or RANGES:
        specification = RANGES[name]
        benchmark = root / name / specification[0]
        try:
            allowed = native_surfaces(root / name, specification)
            paths = sorted((benchmark / "scenarios").glob("*/scenario.json")) + sorted((benchmark / "scenarios").glob("*.json"))
            if not paths:
                raise ValueError("No scenarios found")
            for path in paths:
                try:
                    row = check_scenario(root / name, benchmark, path, specification[1], allowed)
                except (OSError, ValueError, KeyError, TypeError) as error:
                    row = {"scenario": str(path.relative_to(root / name)), "status": "FAIL", "detail": str(error)}
                rows.append({"project": name, **row})
        except (OSError, ValueError, KeyError, TypeError) as error:
            rows.append({"project": name, "status": "FAIL", "detail": str(error)})
    return {"scope": "static-scenario-contract", "status": "PASS" if rows and all(r["status"] == "PASS" for r in rows) else "FAIL",
            "results": rows, "builds": "NOT_RUN", "model_calls": "NOT_RUN", "live_episodes": "NOT_RUN"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", action="append", choices=RANGES)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = audit(projects=args.project)
    for row in report["results"]:
        print(f"{row['status']} {row['project']}/{row.get('scenario', '-')}: {row.get('detail', ', '.join(row.get('success_surfaces', [])))}")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
