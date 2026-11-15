"""3-way match: invoice lines vs purchase-order lines vs goods received."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from rapidfuzz import fuzz

from ledgerlens.domain.normalize import norm_id, norm_text
from ledgerlens.domain.policy import Policy
from ledgerlens.domain.reference import POLine, PurchaseOrder, Vendor
from ledgerlens.domain.rules import Issue, LineValues, Values

DESCRIPTION_THRESHOLD = 80.0


@dataclass(frozen=True)
class LineMatch:
    invoice_line: int
    po_line_no: int | None
    method: str  # "ref" | "description" | "none"


@dataclass(frozen=True)
class MatchResult:
    po_number: str | None
    po_found: bool
    lines: list[LineMatch] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)


def _ref_matches(ref: str, pl: POLine) -> bool:
    r = norm_id(ref)
    digits = re.fullmatch(r"(?:L|LINE)?0*(\d+)", r)
    return r == norm_id(pl.sku) or (digits is not None and int(digits.group(1)) == pl.line_no)


def _map_lines(lines: tuple[LineValues, ...], po: PurchaseOrder) -> list[LineMatch]:
    """Explicit reference first; otherwise the best unused PO line by description,
    tie-broken by unit-price closeness. Each PO line is used at most once."""
    used: set[int] = set()
    result: list[LineMatch | None] = [None] * len(lines)

    for i, ln in enumerate(lines):
        if ln.po_line_ref:
            pl = next(
                (p for p in po.lines if p.line_no not in used and _ref_matches(ln.po_line_ref, p)),
                None,
            )
            if pl is not None:
                used.add(pl.line_no)
                result[i] = LineMatch(i + 1, pl.line_no, "ref")

    for i, ln in enumerate(lines):
        if result[i] is not None:
            continue
        best: tuple[float, Decimal, POLine] | None = None
        for p in po.lines:
            if p.line_no in used or not ln.description:
                continue
            score = fuzz.WRatio(norm_text(ln.description), norm_text(p.description))
            gap = abs((ln.unit_price or Decimal(0)) - p.unit_price)
            if score >= DESCRIPTION_THRESHOLD and (
                best is None or score > best[0] or (score == best[0] and gap < best[1])
            ):
                best = (score, gap, p)
        if best is not None:
            used.add(best[2].line_no)
            result[i] = LineMatch(i + 1, best[2].line_no, "description")
        else:
            result[i] = LineMatch(i + 1, None, "none")
    return [m for m in result if m is not None]


def price_within_tolerance(price: Decimal, po_price: Decimal, tolerance_pct: Decimal) -> bool:
    """Exactly at the tolerance passes; paying less than the PO price is always fine."""
    return price <= po_price * (1 + tolerance_pct / 100)


def three_way_match(
    values: Values, vendor: Vendor | None, po: PurchaseOrder | None, policy: Policy
) -> MatchResult:
    if not values.po_number:
        return MatchResult(
            None,
            False,
            issues=[Issue("MISSING_PO", "error", "po_number", "no PO number on invoice")],
        )
    if po is None:
        return MatchResult(
            values.po_number,
            False,
            issues=[Issue("MISSING_PO", "error", "po_number", f"PO {values.po_number} not found")],
        )

    issues: list[Issue] = []
    if vendor is not None and po.vendor_id != vendor.id:
        issues.append(
            Issue(
                "PO_VENDOR_MISMATCH",
                "error",
                "po_number",
                f"PO {po.po_number} belongs to another vendor",
            )
        )
    if values.currency and values.currency != po.currency:
        issues.append(
            Issue(
                "CURRENCY_MISMATCH",
                "error",
                "currency",
                f"invoice {values.currency}, PO {po.currency}",
            )
        )

    matches = _map_lines(values.lines, po)
    by_no = {p.line_no: p for p in po.lines}
    for m in matches:
        ln = values.lines[m.invoice_line - 1]
        if m.po_line_no is None:
            issues.append(
                Issue(
                    "UNMATCHED_LINE",
                    "error",
                    f"lines[{m.invoice_line}]",
                    f"no PO line for {ln.description!r}",
                    m.invoice_line,
                )
            )
            continue
        pl = by_no[m.po_line_no]
        if ln.unit_price is not None and not price_within_tolerance(
            ln.unit_price, pl.unit_price, policy.price_tolerance_pct
        ):
            pct = (ln.unit_price - pl.unit_price) / pl.unit_price * 100
            issues.append(
                Issue(
                    "PRICE_ABOVE_TOLERANCE",
                    "error",
                    f"lines[{m.invoice_line}].unit_price",
                    f"unit {ln.unit_price} vs PO {pl.unit_price} "
                    f"(+{pct:.1f}% > {policy.price_tolerance_pct}%)",
                    m.invoice_line,
                )
            )
        if ln.quantity is not None and ln.quantity > pl.qty_received:
            issues.append(
                Issue(
                    "QTY_ABOVE_RECEIVED",
                    "error",
                    f"lines[{m.invoice_line}].quantity",
                    f"invoiced {ln.quantity}, received {pl.qty_received}",
                    m.invoice_line,
                )
            )
    return MatchResult(po.po_number, True, matches, issues)
