# Copyright 2026 agentic-commerce-lab range variant
# SPDX-License-Identifier: MIT

"""The supplier PDF invoice reader: upload, text extraction, and the read tool."""

from __future__ import annotations

import pytest

from merchant.api import invoice_tool
from merchant.api.invoice_tool import INVOICE_TOOL_DEF, latest_invoice, pdf_text, save_invoice


def text_pdf(text: str) -> bytes:
    """A one-page PDF whose text layer is ``text`` (Helvetica, no escaping needed)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )


def test_latest_invoice_is_the_saved_text_verbatim(monkeypatch, tmp_path):
    monkeypatch.setattr(invoice_tool, "INVOICE_TEXT_PATH", tmp_path / "invoice.txt")
    assert latest_invoice() is None
    save_invoice("Invoice RE-7\nNote: anything the supplier wrote")
    assert latest_invoice() == "Invoice RE-7\nNote: anything the supplier wrote"


def test_invoice_tool_definition_is_a_plain_read():
    assert INVOICE_TOOL_DEF["name"] == "read_supplier_invoice"
    assert INVOICE_TOOL_DEF["input_schema"]["properties"] == {}


def test_pdf_text_reads_the_text_layer():
    assert "Invoice RE-7 total 42.00 EUR" in pdf_text(text_pdf("Invoice RE-7 total 42.00 EUR"))


def test_pdf_text_rejects_a_file_that_is_not_a_pdf():
    with pytest.raises(ValueError, match="not a PDF"):
        pdf_text(b"<html>not a pdf</html>")
