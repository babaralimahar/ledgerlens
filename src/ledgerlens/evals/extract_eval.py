"""Extraction metrics: per-field exact match, line-item P/R/F1, cost and latency."""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from rapidfuzz import fuzz

from ledgerlens.domain.ground_truth import GTLine, InvoiceGroundTruth
from ledgerlens.domain.normalize import norm_id, norm_text
from ledgerlens.domain.schemas import HEADER_FIELDS, InvoiceExtraction

ID_FIELDS = {"invoice_number", "po_number", "vendor_tax_id"}


def field_equal(name: str, expected: Any, got: Any) -> bool:
    if expected is None or got is None:
        return expected is None and got is None
    if isinstance(expected, Decimal):
        return Decimal(got) == expected
    if name in ID_FIELDS:
        return norm_id(str(got)) == norm_id(str(expected))
    if isinstance(expected, str):
        return norm_text(str(got)) == norm_text(expected)
    return bool(got == expected)


def match_lines(expected: list[GTLine], got: list[dict[str, Any]]) -> tuple[int, int, int]:
    """Greedy: amount within 0.01, then best description similarity. Returns (tp, fp, fn)."""
    unused = list(range(len(got)))
    tp = 0
    for e in expected:
        cands = [
            i
            for i in unused
            if got[i]["amount"] is not None
            and abs(Decimal(got[i]["amount"]) - e.amount) <= Decimal("0.01")
        ]
        if not cands:
            continue
        best = max(
            cands,
            key=lambda i: fuzz.WRatio(
                norm_text(str(got[i]["description"] or "")), norm_text(e.description)
            ),
        )
        if (
            fuzz.WRatio(norm_text(str(got[best]["description"] or "")), norm_text(e.description))
            >= 70
        ):
            tp += 1
            unused.remove(best)
    return tp, len(got) - tp, len(expected) - tp


@dataclass
class ExtractStats:
    n: int = 0
    valid: int = 0
    repaired: int = 0
    invalid: int = 0
    errors: list[str] = field(default_factory=list)
    field_hits: Counter[str] = field(default_factory=Counter)
    field_total: Counter[str] = field(default_factory=Counter)
    tp: int = 0
    fp: int = 0
    fn: int = 0
    costs: list[Decimal] = field(default_factory=list)
    latencies_ms: list[int] = field(default_factory=list)
    unsupported: Counter[str] = field(default_factory=Counter)
    field_errors: list[dict[str, Any]] = field(default_factory=list)

    def add(self, gt: InvoiceGroundTruth, ex: InvoiceExtraction) -> None:
        for name in HEADER_FIELDS:
            self.field_total[name] += 1
            got = getattr(ex, name).value
            exp = getattr(gt, name)
            if field_equal(name, exp, got):
                self.field_hits[name] += 1
            elif len(self.field_errors) < 200:
                self.field_errors.append(
                    {"id": gt.id, "field": name, "expected": str(exp), "got": str(got)}
                )
        lines = [
            {"amount": li.amount.value, "description": li.description.value} for li in ex.lines
        ]
        tp, fp, fn = match_lines(gt.lines, lines)
        self.tp, self.fp, self.fn = self.tp + tp, self.fp + fp, self.fn + fn

    def summary(self) -> dict[str, Any]:
        p = self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0
        r = self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0
        costs = sorted(self.costs)
        lat = sorted(self.latencies_ms)

        def pct(xs: list[Any], q: float) -> Any:
            return xs[min(len(xs) - 1, round(q * (len(xs) - 1)))] if xs else None

        return {
            "n_extracted": self.n,
            "valid_first_try": self.valid,
            "valid_after_repair": self.repaired,
            "invalid": self.invalid,
            "valid_rate": (self.valid + self.repaired) / self.n if self.n else 0.0,
            "field_accuracy": {
                k: self.field_hits[k] / self.field_total[k]
                for k in HEADER_FIELDS
                if self.field_total[k]
            },
            "line_precision": p,
            "line_recall": r,
            "line_f1": 2 * p * r / (p + r) if p + r else 0.0,
            "cost_usd_total": str(sum(costs, Decimal(0))),
            "cost_usd_mean": str(sum(costs, Decimal(0)) / len(costs)) if costs else None,
            "cost_usd_p95": str(pct(costs, 0.95)) if costs else None,
            "latency_ms_p50": statistics.median(lat) if lat else None,
            "latency_ms_p95": pct(lat, 0.95),
            "unsupported_fields": dict(self.unsupported.most_common(15)),
            "errors": self.errors[:20],
        }
