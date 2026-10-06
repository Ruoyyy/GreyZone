#!/usr/bin/env python3
"""Run the shared payload evaluator in the Shopware application environment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from redteam.payload import evaluate_payload  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--source", choices=("promptfoo", "pyrit", "deepteam"), required=True)
    args = parser.parse_args()
    payload = sys.stdin.read().strip()
    if not payload:
        parser.error("Attack text on stdin is required")
    result = evaluate_payload(args.scenario, payload, args.source)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
