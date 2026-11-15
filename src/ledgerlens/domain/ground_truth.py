"""Ground truth for synthetic invoices: what is printed, what is wrong, what should happen."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

Anomaly = Literal[
    "LINE_SUM_MISMATCH",
    "TAX_MISMATCH",
    "DUPLICATE_INVOICE",
    "UNKNOWN_VENDOR",
    "MISSING_PO",
    "PRICE_ABOVE_TOLERANCE",
    "QTY_ABOVE_RECEIVED",
    "FUTURE_DATE",
    "PROMPT_INJECTION",
    "HIDDEN_TEXT",
    "ABSURD_TOTAL",
    "NEEDS_OCR",
]
Route = Literal["auto_approved", "needs_review", "rejected"]


class GTLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    po_line_ref: str | None


class InvoiceGroundTruth(BaseModel):
    """Values exactly as printed on the PDF (anomalies included), plus labels."""

    model_config = ConfigDict(extra="forbid")
    id: str
    pdf: str  # path relative to the dataset directory
    pdf_sha256: str
    layout: str
    vendor_name: str
    vendor_tax_id: str | None
    invoice_number: str
    invoice_date: date
    due_date: date | None
    po_number: str | None
    currency: str
    subtotal: Decimal
    tax: Decimal
    total: Decimal
    lines: list[GTLine]
    anomalies: list[Anomaly]
    expected_route: Route
    duplicate_of: str | None = None
    # How each header value is printed (e.g. "03 Nov 2026", "1,240.00"); lets an oracle
    # extractor produce evidence quotes that really occur in the PDF text.
    rendered: dict[str, str]

    @property
    def defective(self) -> bool:
        return bool(self.anomalies)
