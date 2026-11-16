"""Shared, guarded customer-mail transport."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mail_protocol import *  # noqa: F401,F403
