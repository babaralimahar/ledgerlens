"""Routing policy. Fail-safe by construction: auto-approval needs every condition to hold;
anything missing or uncertain becomes a machine-readable reason for human review."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from ledgerlens.domain.policy import Policy
from ledgerlens.domain.rules import BLOCKING, Issue

RouteStatus = Literal["auto_approved", "needs_review", "rejected"]


@dataclass(frozen=True)
class RoutingDecision:
    status: RouteStatus
    reasons: list[Issue]


def route(
    issues: Sequence[Issue],
    *,
    vendor_known: bool,
    po_found: bool,
    total: Decimal | None,
    currency: str | None,
    policy: Policy,
) -> RoutingDecision:
    codes = {i.code for i in issues}
    if "UNKNOWN_VENDOR" in codes and "MISSING_PO" in codes:
        return RoutingDecision("rejected", [i for i in issues if i.severity in BLOCKING])

    reasons = [i for i in issues if i.severity in BLOCKING]
    # Belt and braces: these hold only if the upstream checks ran. Never infer them.
    if not vendor_known and "UNKNOWN_VENDOR" not in codes:
        reasons.append(Issue("UNKNOWN_VENDOR", "error", "vendor_name", "vendor not resolved"))
    if not po_found and "MISSING_PO" not in codes:
        reasons.append(Issue("MISSING_PO", "error", "po_number", "PO not matched"))
    limit = policy.auto_limit(currency)
    if total is None or total > limit:
        reasons.append(
            Issue(
                "ABOVE_AUTO_LIMIT",
                "info",
                "total",
                f"total {total} {currency or ''} exceeds auto-approval limit {limit}".replace(
                    "  ", " "
                ),
            )
        )
    return RoutingDecision("needs_review" if reasons else "auto_approved", reasons)
