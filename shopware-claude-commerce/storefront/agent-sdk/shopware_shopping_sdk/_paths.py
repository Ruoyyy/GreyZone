# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Repo paths this runtime reads at import time, resolved from this file, and the
environment the Shopware backend is built from."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import dotenv_values

RUNTIME_ROOT = Path(__file__).resolve().parents[1]  # storefront/agent-sdk
STOREFRONT_ROOT = RUNTIME_ROOT.parent  # storefront
REPO_ROOT = STOREFRONT_ROOT.parent
#: The blueprint's shopping skills, vendored verbatim (see NOTICE).
SKILLS_DIR = REPO_ROOT / "vendor" / "skills" / "shopping"
#: Written by ``docker/bootstrap.sh``; fills every shop variable ``.env`` leaves empty.
GENERATED_ENV = REPO_ROOT / "docker" / ".generated.env"

# ``storefront`` and ``shopware_common`` are repo-root packages. The console is started
# as a script (``python storefront/agent-sdk/main.py``), which puts only its own directory
# on the path, so the root goes first, before any of their modules is imported.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_environment() -> None:
    """The same environment the storefront host starts from: ``storefront/.env`` and the
    repo-root ``.env`` (a variable already exported wins), then ``docker/.generated.env``
    for every shop variable still empty."""
    from demo_common import load_demo_env

    load_demo_env(STOREFRONT_ROOT)
    if GENERATED_ENV.exists():
        for key, value in dotenv_values(GENERATED_ENV).items():
            if value and not os.environ.get(key):
                os.environ[key] = value
