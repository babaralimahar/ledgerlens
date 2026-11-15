"""Vendor matching: exact tax id first, then fuzzy name (rapidfuzz), else unknown."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from rapidfuzz import fuzz

from ledgerlens.domain.normalize import norm_id, norm_text
from ledgerlens.domain.reference import Vendor
from ledgerlens.domain.rules import Issue


@dataclass(frozen=True)
class VendorMatch:
    vendor: Vendor | None
    method: Literal["tax_id", "name"] | None
    confidence: float  # 0..1
    issues: list[Issue] = field(default_factory=list)


def match_vendor(
    name: str | None, tax_id: str | None, vendors: Sequence[Vendor], name_threshold: float
) -> VendorMatch:
    if tax_id:
        wanted = norm_id(tax_id)
        for v in vendors:
            if norm_id(v.tax_id) == wanted:
                return VendorMatch(v, "tax_id", 1.0)

    best: tuple[float, Vendor] | None = None
    if name:
        for v in vendors:
            score = fuzz.WRatio(norm_text(name), norm_text(v.name))
            if best is None or score > best[0]:
                best = (score, v)

    if best is not None and best[0] >= name_threshold:
        score, vendor = best
        issues = []
        if tax_id:  # a name that matches but a tax id that doesn't: possible impersonation
            issues.append(
                Issue(
                    "VENDOR_TAX_ID_MISMATCH",
                    "warning",
                    "vendor_tax_id",
                    f"name matches {vendor.name!r} but tax id {tax_id!r} is not on file",
                )
            )
        return VendorMatch(vendor, "name", score / 100, issues)

    detail = f"best name score {best[0]:.0f}" if best else "no name or tax id"
    return VendorMatch(
        None,
        None,
        0.0,
        [Issue("UNKNOWN_VENDOR", "error", "vendor_name", f"not in vendor master ({detail})")],
    )
