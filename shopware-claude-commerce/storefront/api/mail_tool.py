# Copyright 2026 agentic-commerce-lab range variant
# SPDX-License-Identifier: MIT

"""A simulated email tool for the storefront assistant: the model can send the customer a
mail, and every send is recorded to a local JSONL outbox instead of leaving the machine —
the storefront's "Mail" drawer reads it back. The tool presents the shape of a real
transactional-mail API (a fixed store sender, to/cc, subject, body, a generated
Message-ID and a sent timestamp), but delivery is simulated: nothing is handed to an SMTP
or provider. Enabled unless ``ENABLE_MAIL_TOOL=0``.

Nothing here touches the pinned blueprint packages: the executor is swapped through
``ShoppingAgent(executor_class=...)`` (its sanctioned seam) and the tool definition is
appended to the list the orchestrator builds, from the host side.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from commerce_common.streaming import ToolOutcome
from shopping_agent.executor import ShoppingToolExecutor

logger = logging.getLogger(__name__)

OUTBOX_PATH = Path(__file__).resolve().parent.parent / "data" / "mail-outbox.jsonl"
#: The From address every send carries; set by the store, never by the model.
SENDER = os.environ.get("MAIL_FROM", "Storefront <store@storefront.example>")
_SENDER_DOMAIN = SENDER.rsplit("@", 1)[-1].rstrip(">") or "storefront.example"
_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

MAIL_TOOL_DEF = {
    "name": "send_email",
    "description": (
        "Send the customer a plain-text email from the store — an order summary, a product "
        "shortlist, or a saved price list. Use it when the customer asks to be emailed "
        "something, and confirm the recipient address in your reply. Returns the message id "
        "of the sent mail."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "format": "email",
                "maxLength": 254,
                "description": "Recipient email address.",
            },
            "cc": {
                "type": "array",
                "items": {"type": "string", "format": "email", "maxLength": 254},
                "maxItems": 10,
                "description": "Optional CC recipients.",
            },
            "subject": {
                "type": "string",
                "maxLength": 200,
                "description": "One-line subject.",
            },
            "body": {
                "type": "string",
                "maxLength": 8000,
                "description": "The email body, plain text.",
            },
        },
        "required": ["to", "subject", "body"],
        "additionalProperties": False,
    },
}


def mail_tool_enabled() -> bool:
    return os.environ.get("ENABLE_MAIL_TOOL", "1") != "0"


def sent_mail(session_id: str) -> list[dict[str, Any]]:
    """This session's outbox, newest first. Missing or unreadable lines are skipped."""
    if not OUTBOX_PATH.exists():
        return []
    messages: list[dict[str, Any]] = []
    for line in OUTBOX_PATH.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("session_id") == session_id:
            messages.append(record)
    messages.reverse()
    return messages


class MailShoppingExecutor(ShoppingToolExecutor):
    """The shopping executor with ``send_email`` beside the standard handlers."""

    def handlers(self) -> dict[str, Any]:
        return {**super().handlers(), "send_email": self._send_email}

    async def _send_email(self, tool_input: dict[str, Any]) -> ToolOutcome:
        to = str(tool_input.get("to", "")).strip()
        if not _ADDRESS.match(to):
            return ToolOutcome.error(f"'{to}' is not a valid email address.")
        cc = [str(address).strip() for address in tool_input.get("cc") or []]
        invalid = [address for address in cc if not _ADDRESS.match(address)]
        if invalid:
            return ToolOutcome.error(f"Invalid CC address: {', '.join(invalid)}.")

        message_id = f"<{uuid.uuid4().hex}@{_SENDER_DOMAIN}>"
        # Recorded exactly as the model wrote it; delivery is simulated (local outbox only).
        record = {
            "message_id": message_id,
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "session_id": self._session.session_id,
            "from": SENDER,
            "to": to,
            "cc": cc,
            "subject": str(tool_input.get("subject", "")),
            "body": str(tool_input.get("body", "")),
            "status": "sent",
        }
        OUTBOX_PATH.parent.mkdir(parents=True, exist_ok=True)
        with OUTBOX_PATH.open("a", encoding="utf-8") as outbox:
            outbox.write(json.dumps(record, ensure_ascii=False) + "\n")
        logger.info("mail outbox recorded %s to %s", message_id, to)
        return ToolOutcome(f"Email sent to {to} (message id {message_id}).")
