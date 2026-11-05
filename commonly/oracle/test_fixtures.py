"""Integrity checks for fixtures shipped with the benchmark runtime."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_backend_skill_catalog_matches_archived_source():
    bundled = ROOT / "backend/docs/skills/awesome-agent-skills-index.json"
    source = ROOT / "docs/upstream/skills/awesome-agent-skills-index.json"
    assert bundled.read_bytes() == source.read_bytes()
    catalog = json.loads(bundled.read_text())
    assert catalog["items"], "The runtime skill catalog must not be empty"
