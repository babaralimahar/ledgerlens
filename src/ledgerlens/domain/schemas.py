"""The extraction contract: what the LLM must return, field by field, with evidence.

Every field is ``Extracted[T]``: a proposed value, a verbatim quote from the invoice text
that supports it, the page number, and the model's confidence. Values are verified after
the call (``verify_evidence``); an unsupported value has its confidence forced to 0.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from ledgerlens.domain.normalize import parse_amount, parse_date

T = TypeVar("T")


def _lenient_amount(v: Any) -> Any:
    if v is None or v == "":
        return None
    try:
        return parse_amount(v)
    except ValueError:
        return v  # let Pydantic report the error (feeds the repair attempt)


def _lenient_date(v: Any) -> Any:
    if v is None or v == "":
        return None
    try:
        return parse_date(v)
    except ValueError:
        return v


def _lenient_str(v: Any) -> Any:
    if isinstance(v, str):
        v = " ".join(v.split())
        return v or None
    return v


Amount = Annotated[Decimal, BeforeValidator(_lenient_amount)]
Day = Annotated[date, BeforeValidator(_lenient_date)]
Text = Annotated[str, BeforeValidator(_lenient_str)]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quote: str = Field(max_length=300, description="Verbatim text copied from the invoice")
    page: int = Field(ge=1, description="1-based page number the quote is on")


class Extracted(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid")
    value: T | None = Field(description="The field value, or null if absent from the invoice")
    evidence: Evidence | None = Field(description="Quote supporting the value; null if absent")
    confidence: float = Field(ge=0.0, le=1.0)


# Concrete subclasses give the JSON schema short, provider-safe definition names.
class ExtractedText(Extracted[Text]): ...


class ExtractedAmount(Extracted[Amount]): ...


class ExtractedDate(Extracted[Day]): ...


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: ExtractedText
    quantity: ExtractedAmount
    unit_price: ExtractedAmount
    amount: ExtractedAmount
    po_line_ref: ExtractedText


HEADER_FIELDS = (
    "vendor_name",
    "vendor_tax_id",
    "invoice_number",
    "invoice_date",
    "due_date",
    "po_number",
    "subtotal",
    "tax",
    "total",
    "currency",
)
REQUIRED_FIELDS = frozenset(
    {"vendor_name", "invoice_number", "invoice_date", "subtotal", "tax", "total", "currency"}
)
LINE_FIELDS = ("description", "quantity", "unit_price", "amount", "po_line_ref")
REQUIRED_LINE_FIELDS = frozenset({"description", "quantity", "unit_price", "amount"})


class InvoiceExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vendor_name: ExtractedText
    vendor_tax_id: ExtractedText
    invoice_number: ExtractedText
    invoice_date: ExtractedDate
    due_date: ExtractedDate
    po_number: ExtractedText
    subtotal: ExtractedAmount
    tax: ExtractedAmount
    total: ExtractedAmount
    currency: ExtractedText
    lines: list[LineItem] = Field(max_length=200)

    def iter_fields(self) -> Iterator[tuple[str, Extracted[Any]]]:
        """Yield (dotted name, field) for every header and line field."""
        for name in HEADER_FIELDS:
            yield name, getattr(self, name)
        for i, line in enumerate(self.lines, start=1):
            for name in LINE_FIELDS:
                yield f"lines[{i}].{name}", getattr(line, name)

    def required_fields(self) -> Iterator[tuple[str, Extracted[Any]]]:
        for name, f in self.iter_fields():
            short = name.rsplit(".", 1)[-1]
            if name in REQUIRED_FIELDS or (
                name.startswith("lines[") and short in REQUIRED_LINE_FIELDS
            ):
                yield name, f
