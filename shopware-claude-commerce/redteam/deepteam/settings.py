"""Load a Shopware benchmark scenario and DeepTeam model settings."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml
from dotenv import dotenv_values

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from oracle.common import task_category  # noqa: E402
from redteam.scenarios import SUCCESS_FIELD, load_scenario  # noqa: E402


def load_settings(path: Path, selected: str | None, require_credentials: bool):
    config = yaml.safe_load(path.resolve().read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("DeepTeam config must be a mapping")
    scenario_id = selected or config["campaign"]["default_scenario"]
    if scenario_id not in SUCCESS_FIELD:
        raise ValueError(f"Unknown scenario: {scenario_id}")
    scenario_dir = ROOT / "benchmark" / "scenarios" / scenario_id
    scenario = load_scenario(scenario_dir)
    choice = task_category()
    objective = (scenario_dir / scenario["objectiveFile"]).read_text(encoding="utf-8")
    objective = objective.replace("{category}", choice["display"]).replace(
        "{product}", choice["product"]
    ).strip()
    if not objective:
        raise ValueError("Scenario objective is empty")

    if not os.environ.get("ZAI_API_KEY"):
        for env_path, key in (
            (HERE / ".env", "ZAI_API_KEY"),
            (ROOT.parent / "Letta_Issue_Agent" / ".env", "ZAI_API_KEY"),
            (ROOT.parent / "Inbox_Zero_Mail_Agent" / "apps" / "web" / ".env", "LLM_API_KEY"),
        ):
            value = dotenv_values(env_path).get(key) if env_path.exists() else None
            if value:
                os.environ["ZAI_API_KEY"] = value
                break
    for name in ("attacker", "evaluator"):
        model = config["models"][name]
        for field in ("model", "base_url", "api_key_env"):
            if not model.get(field):
                raise ValueError(f"models.{name}.{field} is required")
        if require_credentials and not os.environ.get(model["api_key_env"]):
            raise ValueError(f"Missing credential: {model['api_key_env']}")
    if int(config["campaign"]["timeout_seconds"]) < 1:
        raise ValueError("campaign.timeout_seconds must be positive")
    return config, scenario, scenario_dir, objective
