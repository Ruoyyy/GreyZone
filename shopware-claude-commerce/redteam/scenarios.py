"""Lightweight Shopware scenario validation shared by red-team adapters."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from oracle.common import expand

ROOT = Path(__file__).resolve().parents[1]

SUCCESS_FIELD = {
    "buyer-misdirection": "purchase",
    "buyer-exfil": "exfil",
    "merchant-exfil": "exfil",
    "merchant-floor-breach": "price",
    "merchant-refund": "refund",
}


def load_scenario(scenario_dir: Path) -> dict[str, Any]:
    raw = json.loads((scenario_dir / "scenario.json").read_text(encoding="utf-8"))
    if (raw.get("id") not in SUCCESS_FIELD or raw.get("schemaVersion") != 1
            or raw.get("environment") != "shopware-commerce"
            or scenario_dir.name != raw.get("id")
            or not isinstance(raw.get("victim"), dict)
            or raw["victim"].get("role") not in {"buyer", "merchant"}):
        raise ValueError("Unsupported or mismatched Shopware scenario")
    return expand(raw)


def load_calibration(scenario_id: str) -> dict[str, Any]:
    path = ROOT / "oracle" / "calibrations" / f"{scenario_id}.json"
    return expand(json.loads(path.read_text(encoding="utf-8")))
