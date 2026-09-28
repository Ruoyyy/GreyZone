# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The Managed Agent material derived by hand from the libraries stays in step with them:
``agent.yaml`` resolves to a ``/v1/agents`` body, its enabled MCP tools are exactly what
the merchant MCP server publishes, ``apply_change`` (and ``save_memory``) are the only
``always_ask`` tools, its custom tools are the registry's presentation contracts,
``system.md`` carries every rule the prompt builder emits (or declares the divergence),
and the READMEs list the same tools. Ports the Managed Agents checks of the blueprint's
``scripts/check.py``."""

from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path

import yaml
from mcp.shared.memory import create_connected_server_and_client_session
from merchant_mcp_server import PLATFORM_APPROVAL_SURFACE, build_server

from commerce_common.manifest import resolve
from commerce_common.memory import InMemoryMemoryStore
from commerce_common.skills import SkillRegistry
from merchant.api.agent_config import ShopwareSettings, build_merchant_config
from merchant.api.fake_admin import SALES_CHANNEL_ID, FakeAdmin
from merchant.api.ledger import SqliteChangeLedger
from merchant.api.shopware_backend import ShopwareMerchantBackend
from merchant_agent.prompt import build_static_system
from merchant_agent.tools.registry import build_tools

MANAGED_DIR = Path(__file__).resolve().parents[1]  # merchant/managed-agents
REPO_ROOT = MANAGED_DIR.parents[1]
AGENT_DIR = MANAGED_DIR / "merchant-agent"
MANIFEST = AGENT_DIR / "agent.yaml"
SYSTEM_PROMPT = AGENT_DIR / "system.md"
SKILLS_DIR = REPO_ROOT / "vendor" / "skills" / "merchant"
SERVER_README = MANAGED_DIR / "merchant-mcp-server" / "README.md"
PARENT_README = MANAGED_DIR / "README.md"
ANCHOR = re.compile(r"(?:adapted|omitted):\s*\"([^\"]+)\"")
MERCHANT_SKILLS = {
    "performance-insights",
    "catalog-listings",
    "inventory-operations",
    "pricing-promotions",
    "marketing-campaigns",
}


def normalize_ws(text: str) -> str:
    return " ".join(text.split())


def managed_config():
    """The deployment the hand-derived system.md was written for: the merchant host's
    config under the platform's approval prompt as the approval surface. The prompt is
    derived with host approval on (so it names that surface); the MCP server itself runs
    with it off, because the platform's pause is the approval."""
    return build_merchant_config("Shopware").model_copy(
        update={
            "require_host_approval": True,
            "approval_surface": PLATFORM_APPROVAL_SURFACE,
            "stage_shows_preview": False,
        }
    )


def manifest() -> dict:
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))


def manifest_tools() -> tuple[dict[str, dict], dict[str, dict]]:
    """(enabled MCP tool configs by name, custom tools by name)."""
    mcp_tools: dict[str, dict] = {}
    custom: dict[str, dict] = {}
    for entry in manifest().get("tools", []):
        if entry.get("type") == "mcp_toolset":
            assert entry["default_config"] == {"enabled": False}  # deny by default
            mcp_tools.update({c["name"]: c for c in entry.get("configs", []) if c.get("enabled")})
        elif entry.get("type") == "custom":
            custom[entry["name"]] = entry
    return mcp_tools, custom


def prompt_rules(prompt: str) -> list[str]:
    """The rule bullets outside the Skills section (skills attach natively when hosted)."""
    rules, in_skills = [], False
    for line in prompt.splitlines():
        if line.startswith("# "):
            in_skills = line.strip() == "# Skills"
        elif not in_skills and line.startswith("- "):
            rules.append(normalize_ws(line[2:]))
    return rules


def backticked_after_preamble(line: str) -> set[str]:
    return set(re.findall(r"`([a-z_]+)`", line.split("):", 1)[-1]))


def readme_line(path: Path, marker: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if marker in line:
            return line
    raise AssertionError(f"{path.name}: no '{marker}' line found")


def test_the_manifest_resolves_to_an_agents_body_with_the_vendored_skills(monkeypatch):
    monkeypatch.setenv("MERCHANT_MCP_URL", "https://merchant-mcp.shop.example/mcp")
    with contextlib.redirect_stderr(io.StringIO()):
        body = resolve(MANIFEST, require_env=True)
    assert body["name"] == "Shopware Merchant Agent"
    assert body["model"] == managed_config().model
    assert body["mcp_servers"] == [
        {"type": "url", "name": "merchant", "url": "https://merchant-mcp.shop.example/mcp"}
    ]
    assert body["system"].startswith("You are the merchant assistant for Shopware")
    assert "<!--" not in body["system"]
    skill_dirs = {(AGENT_DIR / e["path"]).resolve() for e in manifest()["skills"]}
    assert skill_dirs == {SKILLS_DIR / name for name in MERCHANT_SKILLS}
    assert all(s["type"] == "custom" and s["version"] == "latest" for s in body["skills"])
    assert body["metadata"]["blueprint"].endswith("fd4d59224ab96b43c6dc6888207c67b3bd5a24cf")


def test_only_read_is_enabled_among_the_built_in_tools():
    (builtin,) = [t for t in manifest()["tools"] if t["type"] == "agent_toolset_20260401"]
    assert builtin["default_config"] == {"enabled": False}
    assert [c["name"] for c in builtin["configs"] if c["enabled"]] == ["read"]


async def test_the_enabled_mcp_tools_are_exactly_what_the_server_publishes():
    """Deny-by-default: a tool the server adds is unusable until the manifest names it,
    and the manifest names no tool the server lacks."""
    settings = ShopwareSettings(
        shop_url="http://shopware.test",
        store_name="Demo Shop",
        local_store=True,
        sales_channel_id=SALES_CHANNEL_ID,
        ledger_dsn=":memory:",
    )
    config = build_merchant_config("Demo Shop").model_copy(
        update={"require_host_approval": False, "stage_shows_preview": False}
    )
    backend = ShopwareMerchantBackend(
        FakeAdmin(), settings, config, ledger=SqliteChangeLedger(config, ":memory:")
    )
    server = build_server(
        backend=backend, memory_store=InMemoryMemoryStore(), config=config, settings=settings
    )
    async with create_connected_server_and_client_session(server) as client:
        published = {tool.name for tool in (await client.list_tools()).tools}
    mcp_tools, _ = manifest_tools()
    assert set(mcp_tools) == published


def test_apply_change_is_always_ask_and_staging_runs_without_confirmation():
    """The platform's pause on apply_change is this path's approval surface; staging is
    Shopware's dry run and records a proposal, so it runs like a read."""
    mcp_tools, _ = manifest_tools()
    policies = {name: c["permission_policy"]["type"] for name, c in mcp_tools.items()}
    always_ask = {name for name, policy in policies.items() if policy == "always_ask"}
    assert always_ask == {"apply_change", "save_memory"}
    assert all(policy == "always_allow" for n, policy in policies.items() if n not in always_ask)
    assert {n for n in policies if n.startswith("stage_")} <= set(policies) - always_ask


def test_the_custom_tools_are_the_registrys_presentation_contracts():
    registry = {t["name"]: t for t in build_tools(managed_config(), skill_names=[])}
    _, custom = manifest_tools()
    assert set(custom) == {name for name in registry if name.startswith("present_")}
    for name, tool in custom.items():
        assert normalize_ws(tool["description"]) == normalize_ws(registry[name]["description"])
        assert tool["input_schema"] == registry[name]["input_schema"]


def test_system_md_carries_every_builder_rule_or_declares_the_divergence():
    document = SYSTEM_PROMPT.read_text(encoding="utf-8")
    body = normalize_ws(document)
    anchors = [normalize_ws(a) for a in ANCHOR.findall(document)]
    prompt = build_static_system(managed_config(), SkillRegistry.from_dir(SKILLS_DIR))
    missing = [
        rule
        for rule in prompt_rules(prompt)
        if rule not in body and not any(anchor in rule for anchor in anchors)
    ]
    assert missing == []
    # The first line is the builder's, house rules and all; the hosted path adds the
    # Store context section, names the connection, and knows no analysis tool.
    assert normalize_ws(prompt.splitlines()[0]) in body
    assert "# Store context" in document and "merchant connection" in document
    assert "load_skill" not in document and "run_analysis" not in document
    assert PLATFORM_APPROVAL_SURFACE in document


def test_the_readmes_list_the_manifests_tools():
    mcp_tools, custom = manifest_tools()
    parent = PARENT_README.read_text(encoding="utf-8")
    counts = re.search(r"(\d+) merchant tools, (\d+) presentation tools", parent)
    assert counts is not None
    assert (int(counts.group(1)), int(counts.group(2))) == (len(mcp_tools), len(custom))
    assert backticked_after_preamble(readme_line(PARENT_README, "**Merchant tools**")) == set(
        mcp_tools
    )
    assert backticked_after_preamble(readme_line(PARENT_README, "**Presentation tools**")) == set(
        custom
    )
    server_readme = SERVER_README.read_text(encoding="utf-8")
    rows = set(re.findall(r"^\| `([a-z_]+)` \|", server_readme, re.MULTILINE))
    assert rows == set(mcp_tools)
