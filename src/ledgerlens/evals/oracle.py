"""An 'oracle' extractor that returns ground truth, with evidence quotes as printed.

It is NOT a model. Uses: (1) isolate rule/routing quality from extraction quality (the
Day 11-12 ablation); (2) drive end-to-end tests deterministically. Reports produced with it
are labelled 'oracle' and must never be presented as model accuracy.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import replace
from decimal import Decimal
from typing import Any

from app.adapters.llm import FakeLLMClient, LLMResult, Message, fake_result
from ledgerlens.domain.ground_truth import InvoiceGroundTruth
from ledgerlens.domain.normalize import norm_text
from ledgerlens.domain.schemas import InvoiceExtraction


def _field(value: Any, key: str, gt: InvoiceGroundTruth, confidence: float) -> dict[str, Any]:
    quote = gt.rendered.get(key)
    if value is None or quote is None:
        return {"value": None, "evidence": None, "confidence": 1.0 if value is None else 0.0}
    v = (
        value.isoformat()
        if hasattr(value, "isoformat")
        else (str(value) if isinstance(value, Decimal) else value)
    )
    return {"value": v, "evidence": {"quote": quote, "page": 1}, "confidence": confidence}


def oracle_payload(gt: InvoiceGroundTruth, confidence: float = 0.95) -> dict[str, Any]:
    header = {
        name: _field(getattr(gt, name), name, gt, confidence)
        for name in (
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
    }
    lines = [
        {
            name: _field(getattr(ln, name), f"lines[{i}].{name}", gt, confidence)
            for name in ("description", "quantity", "unit_price", "amount", "po_line_ref")
        }
        for i, ln in enumerate(gt.lines, start=1)
    ]
    return {**header, "lines": lines}


def oracle_extraction(gt: InvoiceGroundTruth, confidence: float = 0.95) -> InvoiceExtraction:
    return InvoiceExtraction.model_validate(oracle_payload(gt, confidence))


def oracle_llm(ground_truth: Iterable[InvoiceGroundTruth]) -> FakeLLMClient:
    """A FakeLLMClient that answers with the ground truth of whichever invoice is in the prompt."""

    candidates = list(ground_truth)

    def respond(messages: list[Message]) -> LLMResult:
        text = norm_text(str(messages[-1]["content"]))
        # Invoice numbers repeat across vendors, so pick the ground truth whose printed
        # values best match this document (vendor name required).
        best, best_hits = None, 0
        for gt in candidates:
            if norm_text(gt.vendor_name) not in text or norm_text(gt.invoice_number) not in text:
                continue
            hits = sum(1 for v in gt.rendered.values() if norm_text(v) in text)
            if hits > best_hits:
                best, best_hits = gt, hits
        if best is None:
            raise ValueError("oracle: invoice not recognised")
        payload = oracle_payload(best)
        res = fake_result(parsed=payload, text=json.dumps(payload), cost_usd=Decimal(0))
        return replace(res, model="oracle")  # never reported as a model

    return FakeLLMClient(responder=respond, model="oracle")
