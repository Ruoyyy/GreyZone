"""Strict configuration and explicit, cwd-independent credential loading."""
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from scenario import load_scenario, scenario_path


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TargetConfig(StrictModel):
    mcp_url: str = "http://127.0.0.1:8080/mcp"
    web_url: str = "http://127.0.0.1:3000"
    owner: str = "letta-admin"
    repo: str = "AgentThreatDataset"
    title: str = "Question about repo configuration"
    poll_seconds: float = Field(default=5, gt=0)
    reply_timeout_seconds: float = Field(default=900, gt=0)
    request_timeout_seconds: float = Field(default=60, gt=0)

    @field_validator("mcp_url", "web_url")
    @classmethod
    def valid_url(cls, value):
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Expected an HTTP(S) URL")
        if parsed.username or parsed.password:
            raise ValueError("Do not put credentials in URLs")
        return value.rstrip("/")


class CampaignConfig(StrictModel):
    cases: int = Field(default=1, ge=1, le=100, strict=True)
    max_target_calls: int = Field(default=10, ge=1, le=100, strict=True)
    timeout_seconds: float = Field(default=14400, gt=0)
    scenario_file: str
    scenario_id: str = Field(min_length=1)
    success_criteria: str = Field(min_length=1)


class ModelConfig(StrictModel):
    model: str
    base_url: str
    api_key_env: str
    max_tokens: int = Field(default=4096, gt=0)
    timeout_seconds: float = Field(default=120, gt=0)
    extra_body: dict = Field(default_factory=dict)


class ModelsConfig(StrictModel):
    attacker: ModelConfig
    evaluator: ModelConfig


class Settings(StrictModel):
    target: TargetConfig
    campaign: CampaignConfig
    models: ModelsConfig


def load_settings(filename: Path, require_credentials=True, scenario_id=None):
    # Never rely on node --run or the caller's working directory for .env loading.
    load_dotenv(HERE / ".env", override=False)
    load_dotenv(HERE.parents[1] / ".env", override=False)
    filename = filename.resolve()
    data = yaml.safe_load(filename.read_text())
    selected_model = os.getenv("OPENAI_MODEL", "").strip()
    if require_credentials and not selected_model:
        raise ValueError("Set OPENAI_MODEL before running the red-team agent")
    for model_config in data.get("models", {}).values():
        model_config["model"] = selected_model or "test-model"
        model_config["base_url"] = os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        model_config["api_key_env"] = "OPENAI_API_KEY"
    selected = scenario_path(scenario_id) if scenario_id else filename.parent / data["campaign"]["scenario_file"]
    scenario, objective = load_scenario(selected)
    data["campaign"]["scenario_file"] = str(selected)
    data["campaign"]["scenario_id"] = scenario["id"]
    data["campaign"]["max_target_calls"] = scenario["maxTurns"]
    data["campaign"]["success_criteria"] = scenario["successCriteria"]
    settings = Settings.model_validate(data)
    for model in ((settings.models.attacker, settings.models.evaluator) if require_credentials else ()):
        if not os.getenv(model.api_key_env):
            raise ValueError(f"Missing credential: {model.api_key_env}")
    return settings, objective
