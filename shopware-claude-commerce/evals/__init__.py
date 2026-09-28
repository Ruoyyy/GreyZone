# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Behavioral evals for the Shopware shopping and merchant agents.

Snapshot state + one user message → one agent turn with the real model → deterministic
scorers over the final state and the rendered response. ``python -m evals.runner --help``.
"""

from __future__ import annotations

from pathlib import Path

EVALS_ROOT = Path(__file__).resolve().parent
REPO_ROOT = EVALS_ROOT.parent
CASES_DIR = EVALS_ROOT / "cases"
GATES_PATH = EVALS_ROOT / "gates.yaml"
