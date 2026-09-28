# Copyright 2026 shopware AG
# SPDX-License-Identifier: MIT

"""Repo paths this runtime reads at import time, resolved from this file, the ledger it
stages into, and the environment the Shopware backend is built from."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import dotenv_values

RUNTIME_ROOT = Path(__file__).resolve().parents[1]  # merchant/agent-sdk
MERCHANT_ROOT = RUNTIME_ROOT.parent  # merchant
REPO_ROOT = MERCHANT_ROOT.parent
#: The blueprint's merchant skills, vendored verbatim (see NOTICE).
SKILLS_DIR = REPO_ROOT / "vendor" / "skills" / "merchant"
#: Written by ``docker/bootstrap.sh``; fills every shop variable ``.env`` leaves empty.
GENERATED_ENV = REPO_ROOT / "docker" / ".generated.env"

#: This runtime's own change queue. A staged change is approved on the surface that
#: staged it (here the console's y/N prompt), and ``SqliteChangeLedger`` continues its
#: ``chg-000N`` sequence from the file, so the console never shares the FastAPI host's
#: ``MERCHANT_LEDGER_DSN`` file: two processes on one file would hand out one id twice.
LEDGER_DSN_ENV = "MERCHANT_SDK_LEDGER_DSN"
DEFAULT_LEDGER_DSN = f"sqlite:///{RUNTIME_ROOT / '.ledger.db'}"

# ``merchant`` and ``shopware_common`` are repo-root packages. The console is started as
# a script (``python merchant/agent-sdk/main.py``), which puts only its own directory on
# the path, so the root goes first, before any of their modules is imported.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_environment() -> None:
    """The same environment the merchant host starts from: ``merchant/.env`` and the
    repo-root ``.env`` (a variable already exported wins), then ``docker/.generated.env``
    for every shop variable still empty."""
    from demo_common import load_demo_env

    load_demo_env(MERCHANT_ROOT)
    if GENERATED_ENV.exists():
        for key, value in dotenv_values(GENERATED_ENV).items():
            if value and not os.environ.get(key):
                os.environ[key] = value


def ledger_dsn() -> str:
    return (os.environ.get(LEDGER_DSN_ENV) or "").strip() or DEFAULT_LEDGER_DSN
