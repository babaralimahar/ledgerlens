"""The whole decision for one invoice, as one pure function. Used identically by the worker
(with DB-loaded reference data) and by offline evals (with in-memory reference data)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ledgerlens.domain.matching import MatchResult, three_way_match
from ledgerlens.domain.normalize import money, norm_id
from ledgerlens.domain.policy import Policy
from ledgerlens.domain.reference import Reference
from ledgerlens.domain.routing import RoutingDecision, route
from ledgerlens.domain.rules import (
    Issue,
    Values,
    check_confidence,
    check_date_evidence,
    check_dates,
    check_line_amounts,
    check_line_sum,
    check_suspicious_text,
    check_tax,
    check_total,
    check_vendor_currency,
    dedupe_issues,
)
from ledgerlens.domain.schemas import InvoiceExtraction
from ledgerlens.domain.vendors import VendorMatch, match_vendor


@dataclass(frozen=True)
class DocSignals:
    """Facts about the PDF itself, independent of the extraction."""

    visible_pages: Sequence[str]
    hidden_chars: int = 0


@dataclass(frozen=True)
class Decision:
    values: Values
    vendor: VendorMatch
    dedupe_key: str | None
    match: MatchResult
    issues: list[Issue]
    routing: RoutingDecision


def resolve_vendor(ex: InvoiceExtraction, ref: Reference, policy: Policy) -> VendorMatch:
    return match_vendor(
        ex.vendor_name.value, ex.vendor_tax_id.value, ref.vendors, policy.vendor_name_threshold
    )


def dedupe_key(
    vendor_id: str | None, invoice_number: str | None, total: Decimal | None
) -> str | None:
    """(vendor, normalized invoice number, total): the same bill submitted twice."""
    if not vendor_id or not invoice_number or total is None:
        return None
    return f"{vendor_id}|{norm_id(invoice_number)}|{money(total)}"


def decide(
    ex: InvoiceExtraction,
    signals: DocSignals,
    ref: Reference,
    policy: Policy,
    today: date,
    *,
    vendor: VendorMatch,
    is_duplicate: bool,
) -> Decision:
    v = Values.from_extraction(ex)
    issues: list[Issue] = [*vendor.issues]

    issues += check_confidence(ex, policy.confidence_threshold)
    issues += check_line_amounts(v.lines, policy.amount_tolerance)
    amounts = [ln.amount for ln in v.lines]
    if v.subtotal is not None and amounts and all(a is not None for a in amounts):
        issues += check_line_sum(
            [a for a in amounts if a is not None], v.subtotal, policy.amount_tolerance
        )
    if v.subtotal is not None and v.tax is not None and v.total is not None:
        issues += check_total(v.subtotal, v.tax, v.total, policy.amount_tolerance)
    if vendor.vendor is not None and v.subtotal is not None and v.tax is not None:
        issues += check_tax(v.subtotal, v.tax, vendor.vendor.tax_rate, policy.amount_tolerance)
    date_issues = check_date_evidence(ex)
    issues += date_issues
    if not date_issues:  # date checks on a misread or undecidable date give misleading reasons
        issues += check_dates(v.invoice_date, v.due_date, today)
    issues += check_vendor_currency(
        v.currency, vendor.vendor.default_currency if vendor.vendor else None
    )
    issues += check_suspicious_text(signals.visible_pages)
    if signals.hidden_chars > 0:
        issues.append(
            Issue(
                "HIDDEN_TEXT",
                "warning",
                "document",
                f"{signals.hidden_chars} invisible characters ignored",
            )
        )
    key = dedupe_key(vendor.vendor.id if vendor.vendor else None, v.invoice_number, v.total)
    if is_duplicate:
        issues.append(
            Issue(
                "DUPLICATE_INVOICE",
                "error",
                "invoice_number",
                f"same vendor, number and total: {key}",
            )
        )

    match = three_way_match(v, vendor.vendor, ref.po(v.po_number), policy)
    issues = dedupe_issues([*issues, *match.issues])
    routing = route(
        issues,
        vendor_known=vendor.vendor is not None,
        po_found=match.po_found,
        total=v.total,
        currency=v.currency,
        policy=policy,
    )
    return Decision(v, vendor, key, match, issues, routing)
