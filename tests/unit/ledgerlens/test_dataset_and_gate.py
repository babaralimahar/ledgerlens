"""Dataset reproducibility and the routing gate on dev ground truth (Day 8, 11, 12 criteria)."""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

import pytest

from ledgerlens.adapters.pdf import extract_text
from ledgerlens.data.generator import generate
from ledgerlens.domain.decide import DocSignals
from ledgerlens.domain.ground_truth import InvoiceGroundTruth
from ledgerlens.domain.normalize import norm_text
from ledgerlens.domain.policy import Policy
from ledgerlens.evals.dataset import DEFAULT_DIR, load_invoices
from ledgerlens.evals.oracle import oracle_extraction
from ledgerlens.evals.routing_eval import (
    DuplicateTracker,
    Routed,
    decide_offline,
    pipeline_reason,
    score,
)


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("gen")
    generate(42, out)
    return out


def test_same_seed_reproduces_committed_dataset_byte_for_byte(regenerated: Path) -> None:
    for name in ("dev.jsonl", "test.jsonl", "reference.json", "SHA256SUMS"):
        assert (regenerated / name).read_bytes() == (DEFAULT_DIR / name).read_bytes(), name
    committed = sorted((DEFAULT_DIR / "pdfs").glob("*.pdf"))
    assert len(committed) == 150
    for p in committed[:10]:
        assert (
            hashlib.sha256((regenerated / "pdfs" / p.name).read_bytes()).hexdigest()
            == hashlib.sha256(p.read_bytes()).hexdigest()
        )


def test_splits_and_ground_truth_validate() -> None:
    dev, test = load_invoices("dev"), load_invoices("test")
    assert (len(dev.items), len(test.items)) == (100, 50)
    assert not {g.id for g in dev.items} & {g.id for g in test.items}
    for g in dev.items + test.items:
        InvoiceGroundTruth.model_validate(g.model_dump())


def test_duplicates_share_a_split_with_their_original() -> None:
    for split in ("dev", "test"):
        items = load_invoices(split).items
        ids = [g.id for g in items]
        for g in items:
            if g.duplicate_of:
                assert g.duplicate_of in ids and ids.index(g.duplicate_of) < ids.index(g.id)


def test_anomaly_rate_is_about_20_percent_of_regular_invoices() -> None:
    # Label counts only (no system output is computed on the test split here).
    items = load_invoices("dev").items + load_invoices("test").items
    regular = [
        g
        for g in items
        if not set(g.anomalies) & {"PROMPT_INJECTION", "HIDDEN_TEXT", "ABSURD_TOTAL", "NEEDS_OCR"}
    ]
    rate = sum(1 for g in regular if g.anomalies) / len(regular)
    assert 0.17 <= rate <= 0.25
    labels = Counter(a for g in items for a in g.anomalies)
    assert labels["PROMPT_INJECTION"] == 4 and labels["NEEDS_OCR"] == 3


def test_every_printed_value_is_findable_in_the_pdf_text() -> None:
    ds = load_invoices("dev")
    for g in ds.items:
        pdf = extract_text(ds.pdf_bytes(g))
        text = norm_text(" ".join(pdf.pages))
        for key, value in g.rendered.items():
            assert norm_text(value) in text, (g.id, key, value)


def test_routing_gate_on_dev_ground_truth() -> None:
    """With perfect extraction the deterministic half must be perfect: zero false
    auto-approvals, every injected anomaly caught, no clean invoice flagged."""
    ds = load_invoices("dev")
    policy, dups, results = Policy(), DuplicateTracker(), []
    for g in ds.items:
        pdf = extract_text(ds.pdf_bytes(g))
        if pdf.needs_ocr:
            results.append(Routed(g, "needs_review", pipeline_reason("NEEDS_OCR", "-").reasons))
            continue
        d = decide_offline(
            oracle_extraction(g),
            DocSignals(pdf.pages, pdf.hidden_chars),
            ds.reference,
            policy,
            g,
            dups,
            ds.as_of,
        )
        results.append(Routed(g, d.routing.status, d.routing.reasons))
    s = score(results)
    assert s["false_auto_approvals"] == 0
    assert s["anomalies_missed"] == []
    assert s["false_flags_on_clean"] == []
    assert s["needs_review_without_reason"] == []
    assert s["route_accuracy"] == 1.0
    injected = {
        k: v
        for k, v in s["anomaly_catch"].items()
        if k in {"LINE_SUM_MISMATCH", "TAX_MISMATCH", "DUPLICATE_INVOICE"}
    }
    assert all(v["caught"] == v["total"] > 0 for v in injected.values())


def test_injection_fixtures_never_auto_approve_even_with_max_confidence() -> None:
    """Even if a manipulated model reported every field at confidence 1.0, the injection
    invoices still route to review (code decides, not the model)."""
    ds = load_invoices("dev")
    adversarial = [
        g
        for g in ds.items
        if set(g.anomalies) & {"PROMPT_INJECTION", "HIDDEN_TEXT", "ABSURD_TOTAL"}
    ]
    assert len(adversarial) >= 3  # dev only; the held-out fixtures are checked by the held-out run
    for g in adversarial:
        pdf = extract_text(ds.pdf_bytes(g))
        d = decide_offline(
            oracle_extraction(g, confidence=1.0),
            DocSignals(pdf.pages, pdf.hidden_chars),
            ds.reference,
            Policy(),
            g,
            DuplicateTracker(),
            ds.as_of,
        )
        assert d.routing.status != "auto_approved", g.id
