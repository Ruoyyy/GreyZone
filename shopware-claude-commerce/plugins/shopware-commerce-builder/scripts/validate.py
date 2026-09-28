#!/usr/bin/env python3
# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Smoke test for the shopware-commerce-builder plugin.

    python plugins/shopware-commerce-builder/scripts/validate.py [--plugin DIR] [--marketplace FILE]

Checks, without any dependency beyond the standard library:

* the plugin manifest and the marketplace manifest parse as JSON, carry the required fields,
  and agree on the plugin's name, description, and version; the marketplace entry's
  ``source`` resolves to the plugin directory;
* every ``commands/*.md`` has a frontmatter block whose YAML-ish ``key: value`` lines include
  ``description`` (and ``argument-hint``), with no unquoted ``: `` inside a value, and a
  non-empty body that references ``$ARGUMENTS``;
* every ``skills/*/SKILL.md`` has frontmatter with ``name`` (equal to its directory) and
  ``description``, and a non-empty body;
* every relative Markdown link in the plugin's files resolves to an existing file or directory;
* the README lists every command and every skill.

Exit code 0 when everything passes; 1 with one line per finding otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
DEFAULT_PLUGIN_DIR = HERE.parents[1]
DEFAULT_MARKETPLACE = HERE.parents[3] / ".claude-plugin" / "marketplace.json"

FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n(.*)\Z", re.DOTALL)
FRONTMATTER_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$")
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

REQUIRED_PLUGIN_FIELDS = ("name", "description", "version")
REQUIRED_MARKETPLACE_FIELDS = ("name", "owner", "plugins")
REQUIRED_COMMAND_KEYS = ("description", "argument-hint")
REQUIRED_SKILL_KEYS = ("name", "description")
MIN_BODY_CHARS = 200


class Findings:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.checked = 0

    def error(self, message: str) -> None:
        self.errors.append(message)

    def ok(self) -> None:
        self.checked += 1


def parse_frontmatter(text: str, path: Path, findings: Findings) -> tuple[dict[str, str], str]:
    match = FRONTMATTER.match(text)
    if not match:
        findings.error(f"{path}: no frontmatter block (--- ... ---) at the top of the file")
        return {}, text
    fields: dict[str, str] = {}
    for raw_line in match.group(1).splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        entry = FRONTMATTER_LINE.match(line)
        if not entry:
            findings.error(f"{path}: frontmatter line is not `key: value`: {line!r}")
            continue
        key, value = entry.group(1), entry.group(2).strip()
        if key in fields:
            findings.error(f"{path}: frontmatter key {key!r} repeats")
        quoted = len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}
        if quoted:
            value = value[1:-1]
        elif ": " in value or value.endswith(":"):
            # An unquoted `: ` inside a scalar is a YAML parse error; Claude Code then loads the
            # command with empty metadata.
            findings.error(f"{path}: frontmatter value of {key!r} contains an unquoted `: `; quote it")
        if not value:
            findings.error(f"{path}: frontmatter key {key!r} is empty")
        fields[key] = value
    return fields, match.group(2)


def check_body(body: str, path: Path, findings: Findings, *, needs_arguments: bool) -> None:
    stripped = body.strip()
    if len(stripped) < MIN_BODY_CHARS:
        findings.error(f"{path}: body is empty or shorter than {MIN_BODY_CHARS} characters")
    if needs_arguments and "$ARGUMENTS" not in stripped:
        findings.error(f"{path}: command body never reads $ARGUMENTS")


def check_links(text: str, path: Path, findings: Findings) -> None:
    for target in MARKDOWN_LINK.findall(text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target_path = target.split("#", 1)[0]
        if not target_path:
            continue
        resolved = (path.parent / target_path).resolve()
        if not resolved.exists():
            findings.error(f"{path}: link target does not exist: {target}")
        else:
            findings.ok()


def load_json(path: Path, findings: Findings) -> dict | None:
    if not path.exists():
        findings.error(f"{path}: missing")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        findings.error(f"{path}: invalid JSON: {error}")
        return None
    if not isinstance(data, dict):
        findings.error(f"{path}: top level is not an object")
        return None
    findings.ok()
    return data


def check_manifests(plugin_dir: Path, marketplace_path: Path, findings: Findings) -> str | None:
    plugin_manifest = load_json(plugin_dir / ".claude-plugin" / "plugin.json", findings)
    marketplace = load_json(marketplace_path, findings)
    if plugin_manifest is None or marketplace is None:
        return None

    for key in REQUIRED_PLUGIN_FIELDS:
        if not plugin_manifest.get(key):
            findings.error(f"plugin.json: missing or empty {key!r}")
    name = str(plugin_manifest.get("name", ""))
    if name and not KEBAB.match(name):
        findings.error(f"plugin.json: name {name!r} is not kebab-case")
    if name and name != plugin_dir.name:
        findings.error(f"plugin.json: name {name!r} differs from directory {plugin_dir.name!r}")
    author = plugin_manifest.get("author")
    if author is not None and not (isinstance(author, dict) and author.get("name")):
        findings.error("plugin.json: author must be an object with a name")
    keywords = plugin_manifest.get("keywords")
    if keywords is not None and not isinstance(keywords, list):
        findings.error("plugin.json: keywords must be an array")

    for key in REQUIRED_MARKETPLACE_FIELDS:
        if not marketplace.get(key):
            findings.error(f"marketplace.json: missing or empty {key!r}")
    market_name = str(marketplace.get("name", ""))
    if market_name and not KEBAB.match(market_name):
        findings.error(f"marketplace.json: name {market_name!r} is not kebab-case")
    owner = marketplace.get("owner")
    if not (isinstance(owner, dict) and owner.get("name")):
        findings.error("marketplace.json: owner must be an object with a name")
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list):
        findings.error("marketplace.json: plugins must be an array")
        return name
    entries = [entry for entry in plugins if isinstance(entry, dict) and entry.get("name") == name]
    if not entries:
        findings.error(f"marketplace.json: no plugin entry named {name!r}")
        return name
    entry = entries[0]
    source = entry.get("source")
    marketplace_root = marketplace_path.parent.parent
    if not isinstance(source, str) or not source.startswith("./"):
        findings.error(f"marketplace.json: entry {name!r} needs a relative `source` starting with ./")
    elif (marketplace_root / source).resolve() != plugin_dir.resolve():
        findings.error(
            f"marketplace.json: source {source!r} resolves to "
            f"{(marketplace_root / source).resolve()}, not {plugin_dir.resolve()}"
        )
    else:
        findings.ok()
    for key in ("description", "version"):
        if key in entry and entry[key] != plugin_manifest.get(key):
            findings.error(f"marketplace.json: entry {key!r} differs from plugin.json")
    findings.ok()
    return name


def check_commands(plugin_dir: Path, findings: Findings) -> list[str]:
    commands_dir = plugin_dir / "commands"
    files = sorted(commands_dir.glob("*.md")) if commands_dir.is_dir() else []
    if not files:
        findings.error(f"{commands_dir}: no command files")
    names: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        fields, body = parse_frontmatter(text, path, findings)
        for key in REQUIRED_COMMAND_KEYS:
            if key not in fields:
                findings.error(f"{path}: frontmatter lacks {key!r}")
        check_body(body, path, findings, needs_arguments=True)
        check_links(text, path, findings)
        names.append(path.stem)
        findings.ok()
    return names


def check_skills(plugin_dir: Path, findings: Findings) -> list[str]:
    skills_dir = plugin_dir / "skills"
    dirs = sorted(p for p in skills_dir.iterdir() if p.is_dir()) if skills_dir.is_dir() else []
    if not dirs:
        findings.error(f"{skills_dir}: no skill directories")
    names: list[str] = []
    for directory in dirs:
        path = directory / "SKILL.md"
        if not path.exists():
            findings.error(f"{directory}: no SKILL.md")
            continue
        text = path.read_text(encoding="utf-8")
        fields, body = parse_frontmatter(text, path, findings)
        for key in REQUIRED_SKILL_KEYS:
            if key not in fields:
                findings.error(f"{path}: frontmatter lacks {key!r}")
        if fields.get("name") and fields["name"] != directory.name:
            findings.error(f"{path}: name {fields['name']!r} differs from directory {directory.name!r}")
        check_body(body, path, findings, needs_arguments=False)
        check_links(text, path, findings)
        names.append(directory.name)
        findings.ok()
    return names


def check_readme(plugin_dir: Path, commands: list[str], skills: list[str], findings: Findings) -> None:
    path = plugin_dir / "README.md"
    if not path.exists():
        findings.error(f"{path}: missing")
        return
    text = path.read_text(encoding="utf-8")
    for command in commands:
        if f"/{command}" not in text:
            findings.error(f"{path}: does not mention /{command}")
    for skill in skills:
        if f"`{skill}`" not in text:
            findings.error(f"{path}: does not mention skill {skill}")
    check_links(text, path, findings)
    findings.ok()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plugin", type=Path, default=DEFAULT_PLUGIN_DIR, help="plugin directory")
    parser.add_argument("--marketplace", type=Path, default=DEFAULT_MARKETPLACE, help="marketplace.json path")
    args = parser.parse_args()

    findings = Findings()
    plugin_dir = args.plugin.resolve()
    name = check_manifests(plugin_dir, args.marketplace.resolve(), findings)
    commands = check_commands(plugin_dir, findings)
    skills = check_skills(plugin_dir, findings)
    check_readme(plugin_dir, commands, skills, findings)

    print(f"plugin: {name or plugin_dir.name}")
    print(f"commands ({len(commands)}): {', '.join(commands)}")
    print(f"skills ({len(skills)}): {', '.join(skills)}")
    if findings.errors:
        for line in findings.errors:
            print(f"FAIL {line}")
        print(f"{len(findings.errors)} finding(s)")
        return 1
    print(f"ok: {findings.checked} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
