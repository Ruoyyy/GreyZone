"""Compatibility imports for the shared, framework-independent Gitea transport."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gitea_transport import (  # noqa: E402,F401
    IssueTarget, MCPTransport, TargetError, WriteOutcomeUnknown,
    clean_reply, comments_from, connect, decode_result,
)
