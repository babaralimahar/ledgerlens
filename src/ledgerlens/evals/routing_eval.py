"""Routing evaluation: run the full decision on each invoice and compare with labels.

With the oracle extractor this measures the deterministic half (rules, match, routing)
in isolation. With a real model (extract_eval) the same scoring measures the system.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ledgerlens.domain.decide import Decision, DocSignals, decide, resolve_vendor
from ledgerlens.domain.ground_truth import InvoiceGroundTruth
from ledgerlens.domain.policy import Policy
from ledgerlens.domain.reference import Reference
from ledgerlens.domain.routing import RoutingDecision
from ledgerlens.domain.rules import Issue
from ledgerlens.domain.schemas import InvoiceExtraction

# Which issue codes count as "caught" for each injected anomaly label.
CATCHES: dict[str, set[str]] = {
    "LINE_SUM_MISMATCH": {"LINE_SUM_MISMATCH"},
    "TAX_MISMATCH": {"TAX_MISMATCH"},
    "DUPLICATE_INVOICE": {"DUPLICATE_INVOICE"},
    "UNKNOWN_VENDOR": {"UNKNOWN_VENDOR"},
    "MISSING_PO": {"MISSING_PO"},
    "PRICE_ABOVE_TOLERANCE": {"PRICE_ABOVE_TOLERANCE"},
    "QTY_ABOVE_RECEIVED": {"QTY_ABOVE_RECEIVED"},
    # A future date read the wrong way round is still stopped, by the date-evidence rule.
    "FUTURE_DATE": {"FUTURE_DATE", "AMBIGUOUS_DATE", "DATE_EVIDENCE_MISMATCH"},
    "PROMPT_INJECTION": {"SUSPICIOUS_INSTRUCTIONS", "HIDDEN_TEXT"},
    "HIDDEN_TEXT": {"HIDDEN_TEXT"},
    "ABSURD_TOTAL": {"QTY_ABOVE_RECEIVED", "ABOVE_AUTO_LIMIT"},
    "NEEDS_OCR": {"NEEDS_OCR"},
}


# Deliberate caution (policy), not errors: they lower the auto-approval rate but are not
# counted as false flags on clean invoices.
CAUTION_CODES = frozenset({"ABOVE_AUTO_LIMIT", "AMBIGUOUS_DATE"})


@dataclass(frozen=True)
class Routed:
    gt: InvoiceGroundTruth
    status: str
    reasons: list[Issue]

    @property
    def codes(self) -> set[str]:
        return {r.code for r in self.reasons}


@dataclass
class DuplicateTracker:
    """In-memory stand-in for the unique dedupe_key index."""

    seen: set[str] = field(default_factory=set)

    def check_and_add(self, key: str | None) -> bool:
        if key is None:
            return False
        if key in self.seen:
            return True
        self.seen.add(key)
        return False


def decide_offline(
    ex: InvoiceExtraction,
    signals: DocSignals,
    ref: Reference,
    policy: Policy,
    gt: InvoiceGroundTruth,
    dups: DuplicateTracker,
    today: Any,
) -> Decision:
    from ledgerlens.domain.decide import dedupe_key

    vendor = resolve_vendor(ex, ref, policy)
    key = dedupe_key(
        vendor.vendor.id if vendor.vendor else None, ex.invoice_number.value, ex.total.value
    )
    return decide(
        ex, signals, ref, policy, today, vendor=vendor, is_duplicate=dups.check_and_add(key)
    )


def pipeline_reason(code: str, message: str) -> RoutingDecision:
    return RoutingDecision("needs_review", [Issue(code, "error", "document", message)])


def score(results: list[Routed]) -> dict[str, Any]:
    defective = [r for r in results if r.gt.defective]
    clean = [r for r in results if not r.gt.defective]
    false_auto = [r.gt.id for r in defective if r.status == "auto_approved"]
    clean_auto_expected = [r for r in clean if r.gt.expected_route == "auto_approved"]
    caught: Counter[str] = Counter()
    total: Counter[str] = Counter()
    missed: list[str] = []
    for r in defective:
        for label in r.gt.anomalies:
            total[label] += 1
            if r.codes & CATCHES[label]:
                caught[label] += 1
            else:
                missed.append(f"{r.gt.id}:{label}")
    confusion: Counter[str] = Counter(f"{r.gt.expected_route}->{r.status}" for r in results)
    route_match = sum(1 for r in results if r.status == r.gt.expected_route)
    review_without_reason = [
        r.gt.id for r in results if r.status == "needs_review" and not r.reasons
    ]
    false_flags_clean = [
        f"{r.gt.id}:{sorted(r.codes - CAUTION_CODES)}" for r in clean if r.codes - CAUTION_CODES
    ]
    caution = Counter(c for r in clean for c in r.codes & CAUTION_CODES)
    return {
        "n": len(results),
        "caution_reasons_on_clean": dict(caution),
        "n_defective": len(defective),
        "false_auto_approvals": len(false_auto),
        "false_auto_approval_ids": false_auto,
        "clean_auto_approval_rate": (
            sum(1 for r in clean_auto_expected if r.status == "auto_approved")
            / len(clean_auto_expected)
            if clean_auto_expected
            else 0.0
        ),
        "route_accuracy": route_match / len(results) if results else 0.0,
        "anomaly_catch": {k: {"caught": caught[k], "total": total[k]} for k in sorted(total)},
        "anomalies_missed": missed,
        "false_flags_on_clean": false_flags_clean,
        "needs_review_without_reason": review_without_reason,
        "confusion": dict(sorted(confusion.items())),
    }
