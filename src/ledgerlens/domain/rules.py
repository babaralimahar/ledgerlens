"""Deterministic validation rules. Pure functions: values in, issues out, no I/O."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from ledgerlens.domain.normalize import CENT, money
from ledgerlens.domain.schemas import InvoiceExtraction

Severity = Literal["info", "warning", "error"]
BLOCKING: frozenset[Severity] = frozenset({"warning", "error"})


@dataclass(frozen=True)
class Issue:
    code: str
    severity: Severity
    field: str
    message: str
    line: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LineValues:
    description: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    amount: Decimal | None
    po_line_ref: str | None


@dataclass(frozen=True)
class Values:
    """Plain extracted values (evidence and confidence stripped) for the rules."""

    vendor_name: str | None
    vendor_tax_id: str | None
    invoice_number: str | None
    invoice_date: date | None
    due_date: date | None
    po_number: str | None
    subtotal: Decimal | None
    tax: Decimal | None
    total: Decimal | None
    currency: str | None
    lines: tuple[LineValues, ...]

    @classmethod
    def from_extraction(cls, ex: InvoiceExtraction) -> Values:
        currency = ex.currency.value.upper() if ex.currency.value else None
        return cls(
            vendor_name=ex.vendor_name.value,
            vendor_tax_id=ex.vendor_tax_id.value,
            invoice_number=ex.invoice_number.value,
            invoice_date=ex.invoice_date.value,
            due_date=ex.due_date.value,
            po_number=ex.po_number.value,
            subtotal=ex.subtotal.value,
            tax=ex.tax.value,
            total=ex.total.value,
            currency=currency,
            lines=tuple(
                LineValues(
                    li.description.value,
                    li.quantity.value,
                    li.unit_price.value,
                    li.amount.value,
                    li.po_line_ref.value,
                )
                for li in ex.lines
            ),
        )


# ------------------------------------------------------------------ arithmetic


def check_line_amounts(lines: Sequence[LineValues], tol: Decimal = CENT) -> list[Issue]:
    issues = []
    for i, ln in enumerate(lines, start=1):
        if ln.quantity is None or ln.unit_price is None or ln.amount is None:
            continue
        expected = money(ln.quantity * ln.unit_price)
        if abs(expected - money(ln.amount)) > tol:
            issues.append(
                Issue(
                    "LINE_AMOUNT_MISMATCH",
                    "error",
                    f"lines[{i}].amount",
                    f"{ln.quantity} x {ln.unit_price} = {expected}, invoice says {ln.amount}",
                    i,
                )
            )
    return issues


def check_line_sum(lines: list[Decimal], subtotal: Decimal, tol: Decimal = CENT) -> list[Issue]:
    s = money(sum(lines, Decimal("0")))
    if abs(s - money(subtotal)) > tol:
        return [
            Issue("LINE_SUM_MISMATCH", "error", "subtotal", f"lines sum {s} != subtotal {subtotal}")
        ]
    return []


def check_total(
    subtotal: Decimal, tax: Decimal, total: Decimal, tol: Decimal = CENT
) -> list[Issue]:
    expected = money(subtotal + tax)
    if abs(expected - money(total)) > tol:
        return [
            Issue(
                "TOTAL_MISMATCH",
                "error",
                "total",
                f"{subtotal} + {tax} = {expected} != total {total}",
            )
        ]
    return []


def check_tax(subtotal: Decimal, tax: Decimal, rate: Decimal, tol: Decimal = CENT) -> list[Issue]:
    expected = money(subtotal * rate)
    if abs(expected - money(tax)) > tol:
        pct = (rate * 100).normalize()
        return [
            Issue(
                "TAX_MISMATCH",
                "error",
                "tax",
                f"{pct}% of {subtotal} = {expected}, invoice says {tax}",
            )
        ]
    return []


# ------------------------------------------------------------------ dates and currency


def check_dates(invoice_date: date | None, due_date: date | None, today: date) -> list[Issue]:
    issues = []
    if invoice_date is not None and invoice_date > today:
        issues.append(
            Issue("FUTURE_DATE", "error", "invoice_date", f"{invoice_date} is after {today}")
        )
    if invoice_date is not None and due_date is not None and due_date < invoice_date:
        issues.append(
            Issue(
                "DUE_BEFORE_INVOICE",
                "error",
                "due_date",
                f"due {due_date} before invoice {invoice_date}",
            )
        )
    return issues


def check_vendor_currency(currency: str | None, vendor_currency: str | None) -> list[Issue]:
    if currency and vendor_currency and currency != vendor_currency:
        return [
            Issue(
                "CURRENCY_MISMATCH",
                "error",
                "currency",
                f"invoice in {currency}, vendor bills in {vendor_currency}",
            )
        ]
    return []


# ------------------------------------------------------------------ extraction quality


def check_confidence(ex: InvoiceExtraction, threshold: float) -> list[Issue]:
    """Missing or low-confidence required fields (incl. failed evidence checks, which set
    confidence to 0) block auto-approval."""
    issues = []
    for name, f in ex.required_fields():
        if f.value is None:
            issues.append(Issue("LOW_CONFIDENCE_FIELD", "warning", name, "required field missing"))
        elif f.confidence < threshold:
            why = (
                "evidence not found in text"
                if f.confidence == 0
                else f"confidence {f.confidence:.2f}"
            )
            issues.append(Issue("LOW_CONFIDENCE_FIELD", "warning", name, why))
    if not ex.lines:
        issues.append(Issue("LOW_CONFIDENCE_FIELD", "warning", "lines", "no line items extracted"))
    return issues


_INJECTION = re.compile(
    r"(?ix)"
    r"ignore\s+(all\s+|any\s+)?(previous|prior|above|earlier)\s+instructions"
    r"|disregard\s+(all\s+|the\s+)?(previous|prior|above)"
    r"|\b(system|assistant)\s*:"
    r"|\bpre-?approved\b"
    r"|mark\s+(this\s+)?(invoice\s+)?as\s+(approved|paid)"
    r"|set\s+(the\s+)?(status|confidence)\s+to"
    r"|\byou\s+are\s+(an?\s+)?(ai|assistant|language\s+model)"
    r"|\b(ai|llm)\s+(systems?|models?|assistants?)\b"
)


def check_suspicious_text(pages: Iterable[str]) -> list[Issue]:
    """Defence in depth: the LLM cannot approve anything (it only fills a schema), but text
    addressed to an AI is a fraud signal a human should see."""
    for n, page in enumerate(pages, start=1):
        m = _INJECTION.search(page)
        if m:
            return [
                Issue(
                    "SUSPICIOUS_INSTRUCTIONS",
                    "warning",
                    "document",
                    f"page {n} contains instruction-like text: {m.group(0)[:60]!r}",
                )
            ]
    return []


def dedupe_issues(issues: Iterable[Issue]) -> list[Issue]:
    seen: set[tuple[str, str, int | None]] = set()
    out = []
    for i in issues:
        key = (i.code, i.field, i.line)
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out
