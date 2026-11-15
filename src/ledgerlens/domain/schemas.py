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


# ------------------------------------------------------------------ wire format
#
# What the model is asked to return. Flat on purpose: some providers (Gemini's
# OpenAI-compatible endpoint, Nov 2026) reject the nested strict schema above as too complex,
# while accepting this one. ``from_wire`` converts it to the strict form, which is then
# validated exactly as before, so nothing is checked less strictly.

_WIRE_DESCRIPTIONS = {
    "value": "The value as printed (dates YYYY-MM-DD, amounts as plain numbers), or null",
    "quote": "Shortest verbatim text from the invoice that shows the value, or null",
    "page": "1-based page number of the quote",
    "confidence": "0.0-1.0",
}


def _wire_field() -> dict[str, Any]:
    nullable_str = {"type": ["string", "null"]}
    return {
        "type": "object",
        "properties": {
            "value": {**nullable_str, "description": _WIRE_DESCRIPTIONS["value"]},
            "quote": {**nullable_str, "description": _WIRE_DESCRIPTIONS["quote"]},
            "page": {"type": "integer", "description": _WIRE_DESCRIPTIONS["page"]},
            "confidence": {"type": "number", "description": _WIRE_DESCRIPTIONS["confidence"]},
        },
        "required": ["value", "quote", "page", "confidence"],
    }


def wire_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            **{name: _wire_field() for name in HEADER_FIELDS},
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {name: _wire_field() for name in LINE_FIELDS},
                    "required": list(LINE_FIELDS),
                },
            },
        },
        "required": [*HEADER_FIELDS, "lines"],
    }


def _from_wire_field(f: Any) -> Any:
    if (
        not isinstance(f, dict) or "evidence" in f
    ):  # already strict (or garbage: let Pydantic say so)
        return f
    value, quote = f.get("value"), f.get("quote")
    page = f.get("page") or 1
    return {
        "value": value,
        "evidence": {"quote": quote, "page": page} if value is not None and quote else None,
        "confidence": f.get("confidence", 0.0),
    }


def from_wire(raw: Any) -> Any:
    """Wire JSON -> strict ``InvoiceExtraction`` payload. Non-dicts pass through unchanged."""
    if not isinstance(raw, dict):
        return raw
    out: dict[str, Any] = {k: _from_wire_field(v) for k, v in raw.items() if k != "lines"}
    lines = raw.get("lines")
    if isinstance(lines, list):
        out["lines"] = [
            {k: _from_wire_field(v) for k, v in ln.items()} if isinstance(ln, dict) else ln
            for ln in lines
        ]
    elif "lines" in raw:
        out["lines"] = lines
    return out
