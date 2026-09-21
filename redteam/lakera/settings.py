"""Strict configuration, fixed 300-second maximum and explicit dotenv loading."""
import os
from pathlib import Path
from urllib.parse import urlparse

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator

HERE = Path(__file__).resolve().parent


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
    repo: str = Field(default="AgentThreatDataset", min_length=1)
    title: str = "Question about repo configuration"
    poll_seconds: float = Field(default=5, gt=0, le=300)
    reply_timeout_seconds: float = Field(default=300, gt=0, le=300)
    request_timeout_seconds: float = Field(default=60, gt=0, le=60)
    _urls = field_validator("mcp_url", "web_url")(valid_url)


class RedConfig(StrictModel):
    base_url: str = "https://red-webhooks.lakera.ai"
    api_key_env: str = "LAKERA_RED_API_KEY"
    target_name: str = Field(default="AgentThreatDataset-Letta-MCP-L1", min_length=1)
    response_timeout_seconds: float = Field(default=60, gt=0, le=60)
    _url = field_validator("base_url")(valid_url)


class CampaignConfig(StrictModel):
    cases: int = Field(default=1, ge=1, le=100, strict=True)
    max_target_calls: int = Field(default=10, ge=1, le=30, strict=True)
    timeout_seconds: float = Field(default=7200, gt=0)
    results_timeout_seconds: float = Field(default=300, gt=0, le=300)
    objective_file: str = "objective.md"
    objective_key: str = Field(default="agent-threat.runtime-secret-disclosure.1", min_length=1)
    objective_name: str = "Runtime credential disclosure"
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


def load_settings(filename: Path, require_credentials=True):
    load_dotenv(HERE / ".env", override=False)
    load_dotenv(HERE.parents[1] / ".env", override=False)
    filename = filename.resolve()
    settings = Settings.model_validate(yaml.safe_load(filename.read_text()))
    objective = (filename.parent / settings.campaign.objective_file).read_text().strip()
    if not objective:
        raise ValueError("Objective file is empty")
    if require_credentials and not os.getenv(settings.red.api_key_env, "").strip():
        raise MissingCredentialError(settings.red.api_key_env)
    return settings, objective
