"""Business thresholds in one place. Changing any of these is a reviewed config change."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType

from ledgerlens.domain.normalize import CENT


def _limits(**kw: str) -> Mapping[str, Decimal]:
    return MappingProxyType({k: Decimal(v) for k, v in kw.items()})


@dataclass(frozen=True)
class Policy:
    price_tolerance_pct: Decimal = Decimal("2")  # unit price may exceed PO price by <= 2 %
    amount_tolerance: Decimal = CENT  # rounding slack for arithmetic checks
    confidence_threshold: float = 0.8  # below this a required field goes to review
    vendor_name_threshold: float = 90.0  # rapidfuzz WRatio for a name-only vendor match
    # Auto-approval cap per currency (no FX: an unlisted currency is never auto-approved).
    auto_approve_limits: Mapping[str, Decimal] = field(
        default_factory=lambda: _limits(USD="5000", EUR="4600", GBP="4000", PKR="1400000")
    )
    # A clerk may approve/reject up to this amount; approvers have no limit.
    clerk_decision_limits: Mapping[str, Decimal] = field(
        default_factory=lambda: _limits(USD="5000", EUR="4600", GBP="4000", PKR="1400000")
    )

    def auto_limit(self, currency: str | None) -> Decimal:
        return self.auto_approve_limits.get(currency or "", Decimal(0))

    def clerk_limit(self, currency: str | None) -> Decimal:
        return self.clerk_decision_limits.get(currency or "", Decimal(0))
