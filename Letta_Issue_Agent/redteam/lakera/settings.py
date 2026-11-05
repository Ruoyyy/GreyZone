"""Strict configuration, fixed 300-second maximum and explicit dotenv loading."""
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


class MissingCredentialError(ValueError):
    def __init__(self, variable):
        self.variable = variable
        super().__init__(f"Missing credential: {variable}")


def valid_url(value):
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Expected an HTTP(S) URL without embedded credentials")
    return value.rstrip("/")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class TargetConfig(StrictModel):
    mcp_url: str = "http://127.0.0.1:8080/mcp"
    web_url: str = "http://127.0.0.1:3000"
    owner: str = Field(default="letta-admin", min_length=1)
    repo: str = Field(default="GreyZone", min_length=1)
    title: str = "Question about repo configuration"
    poll_seconds: float = Field(default=5, gt=0, le=300)
    reply_timeout_seconds: float = Field(default=300, gt=0, le=300)
    request_timeout_seconds: float = Field(default=60, gt=0, le=60)
    _urls = field_validator("mcp_url", "web_url")(valid_url)


class RedConfig(StrictModel):
    base_url: str = "https://red-webhooks.lakera.ai"
    api_key_env: str = "LAKERA_RED_API_KEY"
    target_name: str = Field(default="GreyZone-Letta-MCP-L1", min_length=1)
    response_timeout_seconds: float = Field(default=60, gt=0, le=60)
    _url = field_validator("base_url")(valid_url)


class CampaignConfig(StrictModel):
    cases: int = Field(default=1, ge=1, le=100, strict=True)
    max_target_calls: int = Field(default=10, ge=1, le=30, strict=True)
    timeout_seconds: float = Field(default=7200, gt=0)
    results_timeout_seconds: float = Field(default=300, gt=0, le=300)
    scenario_file: str
    scenario_id: str = Field(min_length=1)
    objective_key: str = Field(min_length=1)
    objective_name: str = Field(min_length=1)
    success_indicators: list[str] = Field(min_length=1)


class Profile(StrictModel):
    app_description: str = Field(min_length=1)
    allowed_actions: list[str] = Field(min_length=1)
    forbidden_actions: list[str] = Field(min_length=1)


class Settings(StrictModel):
    target: TargetConfig = Field(default_factory=TargetConfig)
    red: RedConfig = Field(default_factory=RedConfig)
    campaign: CampaignConfig
    profile: Profile

    @property
    def sdk_idle_seconds(self):
        # Relay inactivity is distinct from the target reply deadline. Allow for
        # one MCP write, the reply, its upload, and a 60s scheduling margin.
        return (self.target.request_timeout_seconds + self.target.reply_timeout_seconds
                + self.red.response_timeout_seconds + 60)


def load_settings(filename: Path, require_credentials=True, scenario_id=None):
    load_dotenv(HERE / ".env", override=False)
    load_dotenv(HERE.parents[1] / ".env", override=False)
    filename = filename.resolve()
    data = yaml.safe_load(filename.read_text())
    selected = scenario_path(scenario_id) if scenario_id else filename.parent / data["campaign"]["scenario_file"]
    scenario, objective = load_scenario(selected)
    data["campaign"]["scenario_file"] = str(selected)
    data["campaign"]["scenario_id"] = scenario["id"]
    data["campaign"].update(
        max_target_calls=scenario["maxTurns"],
        objective_key=f"agent-threat.{scenario['id']}.1",
        objective_name=scenario["name"],
        success_indicators=[scenario["successCriteria"]],
    )
    data["profile"]["forbidden_actions"] = [scenario["policy"]]
    settings = Settings.model_validate(data)
    if require_credentials and not os.getenv(settings.red.api_key_env, "").strip():
        raise MissingCredentialError(settings.red.api_key_env)
    return settings, objective
