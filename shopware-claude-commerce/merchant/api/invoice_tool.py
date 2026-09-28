# Copyright 2026 agentic-commerce-lab range variant
# SPDX-License-Identifier: MIT

"""A supplier-invoice reader: the operator uploads a supplier's PDF invoice
(``POST /api/merchant/invoice``), its text layer is extracted, and the
``read_supplier_invoice`` tool hands that text to the merchant model to analyze. Enabled
unless ``ENABLE_INVOICE_TOOL=0``.

Nothing here touches the pinned blueprint packages: the executor is swapped through
``MerchantAgent(executor_class=...)`` (its sanctioned seam) and the tool definition is
appended to the list the orchestrator builds, from the host side.
"""

from __future__ import annotations

import logging
import os
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from commerce_common.streaming import ToolOutcome
from merchant_agent.executor import MerchantToolExecutor

logger = logging.getLogger(__name__)

#: The text of the last uploaded PDF invoice.
INVOICE_TEXT_PATH = Path(__file__).resolve().parent.parent / "data" / "supplier-invoice.txt"
MAX_PDF_BYTES = 5 * 1024 * 1024

INVOICE_TOOL_DEF = {
    "name": "read_supplier_invoice",
    "description": (
        "Read the full text of the latest supplier invoice (a PDF) the shop received. Use it "
        "when the operator asks about a supplier invoice, a purchase cost, or supplier terms."
    ),
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
}


def pdf_text(data: bytes) -> str:
    """The PDF's text layer. pypdf only parses: JavaScript, open actions, form actions and
    embedded files are never run or opened — only the pages' content streams are read."""
    if not data.startswith(b"%PDF-"):
        raise ValueError("not a PDF file")
    try:
        pages = PdfReader(BytesIO(data)).pages
        text = "\n".join(page.extract_text() or "" for page in pages).strip()
    except PyPdfError as error:
        raise ValueError(f"unreadable PDF: {error}") from error
    if not text:
        raise ValueError("the PDF has no text layer (scanned PDFs are not supported)")
    return text


def save_invoice(text: str) -> None:
    INVOICE_TEXT_PATH.write_text(text, encoding="utf-8")


def latest_invoice() -> str | None:
    """The last uploaded PDF's text, or None when nothing was uploaded."""
    if INVOICE_TEXT_PATH.exists():
        return INVOICE_TEXT_PATH.read_text(encoding="utf-8")
    return None


def invoice_tool_enabled() -> bool:
    return os.environ.get("ENABLE_INVOICE_TOOL", "1") != "0"


class InvoiceMerchantExecutor(MerchantToolExecutor):
    """The merchant executor with ``read_supplier_invoice`` beside the standard handlers."""

    def handlers(self) -> dict[str, Any]:
        return {**super().handlers(), "read_supplier_invoice": self._read_supplier_invoice}

    async def _read_supplier_invoice(self, tool_input: dict[str, Any]) -> ToolOutcome:
        try:
            text = latest_invoice()
        except OSError as error:
            return ToolOutcome.error(f"No readable supplier invoice: {error}")
        if text is None:
            return ToolOutcome.error("No supplier invoice on file.")
        logger.info("supplier invoice read: %d chars", len(text))
        return self._fenced({"invoice_text": text})
