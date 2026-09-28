# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""The Managed Agent material derived by hand from the libraries stays in step with them:
``agent.yaml`` resolves to a ``/v1/agents`` body, its enabled MCP tools are exactly what
the storefront MCP server publishes, its custom tools are the registry's presentation
contracts for the storefront host's config, ``system.md`` carries every rule the prompt
builder emits (or declares the divergence), and the READMEs list the same tools. Ports the
Managed Agents checks of the blueprint's ``scripts/check.py``."""

from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path

import yaml
from mcp.shared.memory import create_connected_server_and_client_session
from storefront_mcp_server import build_server

from commerce_common.manifest import resolve
from commerce_common.memory import InMemoryMemoryStore
from commerce_common.skills import SkillRegistry
from shopping_agent.prompt import build_static_system
from shopping_agent.tools.registry import build_tools
from storefront.api.agent_config import build_shopping_config

MANAGED_DIR = Path(__file__).resolve().parents[1]  # storefront/managed-agents
REPO_ROOT = MANAGED_DIR.parents[1]
AGENT_DIR = MANAGED_DIR / "shopping-agent"
MANIFEST = AGENT_DIR / "agent.yaml"
SYSTEM_PROMPT = AGENT_DIR / "system.md"
SKILLS_DIR = REPO_ROOT / "vendor" / "skills" / "shopping"
SERVER_README = MANAGED_DIR / "storefront-mcp-server" / "README.md"
PARENT_README = MANAGED_DIR / "README.md"
ANCHOR = re.compile(r"(?:adapted|omitted):\s*\"([^\"]+)\"")
SHOPPING_SKILLS = {
    "search-discovery",
    "planning-goals",
    "purchase-research",
    "memory-personalization",
    "customer-care",
}


def normalize_ws(text: str) -> str:
    return " ".join(text.split())


def managed_config():
    """The deployment the hand-derived system.md was written for: the storefront host's."""
    return build_shopping_config("Shopware")


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
    monkeypatch.setenv("STOREFRONT_MCP_URL", "https://commerce-mcp.shop.example/mcp")
    with contextlib.redirect_stderr(io.StringIO()):
        body = resolve(MANIFEST, require_env=True)
    assert body["name"] == "Shopware Shopping Agent"
    assert body["model"] == managed_config().model
    assert body["mcp_servers"] == [
        {"type": "url", "name": "storefront", "url": "https://commerce-mcp.shop.example/mcp"}
    ]
    assert body["system"].startswith("You are the store assistant for Shopware")
    assert "<!--" not in body["system"]
    skill_dirs = {(AGENT_DIR / e["path"]).resolve() for e in manifest()["skills"]}
    assert skill_dirs == {SKILLS_DIR / name for name in SHOPPING_SKILLS}
    assert all(s["type"] == "custom" and s["version"] == "latest" for s in body["skills"])
    assert body["metadata"]["blueprint"].endswith("fd4d59224ab96b43c6dc6888207c67b3bd5a24cf")


def test_only_read_is_enabled_among_the_built_in_tools():
    (builtin,) = [t for t in manifest()["tools"] if t["type"] == "agent_toolset_20260401"]
    assert builtin["default_config"] == {"enabled": False}
    assert [c["name"] for c in builtin["configs"] if c["enabled"]] == ["read"]


async def test_the_enabled_mcp_tools_are_exactly_what_the_server_publishes(monkeypatch):
    """Deny-by-default: a tool the server adds is unusable until the manifest names it,
    and the manifest names no tool the server lacks."""
    monkeypatch.setenv("SHOPWARE_URL", "http://shopware.test")
    monkeypatch.setenv("SHOPWARE_SALES_CHANNEL_ACCESS_KEY", "test-key")
    monkeypatch.delenv("UCP_AGENT_SIGNING_KEY_PEM_FILE", raising=False)
    from storefront.api.shopware_backend import ShopwareStorefrontBackend
    from storefront.api.store_api import StoreApiClient
    from storefront.api.ucp_client import UcpClient

    backend = ShopwareStorefrontBackend(
        UcpClient("http://shopware.test", signer=None),
        store_api=StoreApiClient("http://shopware.test", access_key="test-key"),
    )
    server = build_server(backend=backend, memory_store=InMemoryMemoryStore())
    async with create_connected_server_and_client_session(server) as client:
        published = {tool.name for tool in (await client.list_tools()).tools}
    mcp_tools, _ = manifest_tools()
    assert set(mcp_tools) == published


def test_writes_pause_for_the_host_and_reads_run_without_confirmation():
    mcp_tools, _ = manifest_tools()
    policies = {name: c["permission_policy"]["type"] for name, c in mcp_tools.items()}
    always_ask = {name for name, policy in policies.items() if policy == "always_ask"}
    assert always_ask == {"add_to_cart", "update_cart_item", "remove_from_cart", "save_memory"}
    assert all(policy == "always_allow" for n, policy in policies.items() if n not in always_ask)


def test_the_custom_tools_are_the_registrys_presentation_contracts():
    registry = {t["name"]: t for t in build_tools(managed_config(), skill_names=[])}
    _, custom = manifest_tools()
    presentation = {
        name for name, tool in registry.items() if name.startswith("present_") or name == "checkout"
    }
    assert set(custom) == presentation
    assert "present_disclosure" in custom  # enable_disclosures is on for Shopware
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
    # The first line is the builder's, brand voice and all; the hosted path adds the
    # Customer context section and names the connection.
    assert normalize_ws(prompt.splitlines()[0]) in body
    assert "# Customer context" in document and "storefront connection" in document
    assert "load_skill" not in document


def test_the_readmes_list_the_manifests_tools():
    mcp_tools, custom = manifest_tools()
    parent = PARENT_README.read_text(encoding="utf-8")
    counts = re.search(r"(\d+) storefront tools, (\d+) presentation tools", parent)
    assert counts is not None
    assert (int(counts.group(1)), int(counts.group(2))) == (len(mcp_tools), len(custom))
    assert backticked_after_preamble(readme_line(PARENT_README, "**Storefront tools**")) == set(
        mcp_tools
    )
    assert backticked_after_preamble(readme_line(PARENT_README, "**Presentation tools**")) == set(
        custom
    )
    server_readme = SERVER_README.read_text(encoding="utf-8")
    rows = set(re.findall(r"^\| `([a-z_]+)` \|", server_readme, re.MULTILINE))
    assert rows == set(mcp_tools)
