"""LedgerLens evaluation CLI.

# Rules + match + routing on ground truth (oracle extraction; no model, no cost):
uv run python -m ledgerlens.evals routing --split dev

# The whole system with the configured model (needs a real LLM provider):
uv run python -m ledgerlens.evals system --split dev --sample 20 --max-cost 1.00

# Held-out: once per project, full set, logged in evals/HELDOUT_LOG.md
uv run python -m ledgerlens.evals system --split test
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.adapters.llm import LLMClient, LLMError, load_pricing
from app.adapters.llm_factory import build_llm_client
from app.evals.dataset import DatasetError
from app.evals.heldout import HeldOutAlreadyRun, check_heldout, record_heldout
from app.evals.report import git_sha
from ledgerlens.adapters.pdf import PdfUnreadable, extract_text
from ledgerlens.config import LedgerSettings, get_ledger_settings
from ledgerlens.domain.decide import DocSignals
from ledgerlens.evals.dataset import DEFAULT_DIR, InvoiceDataset, load_invoices
from ledgerlens.evals.extract_eval import ExtractStats
from ledgerlens.evals.oracle import oracle_extraction, oracle_llm
from ledgerlens.evals.routing_eval import (
    DuplicateTracker,
    Routed,
    decide_offline,
    pipeline_reason,
    score,
)
from ledgerlens.services.extract import ExtractionInvalid, extract


def _default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    raise TypeError(type(o).__name__)


def _write(out_dir: Path, stem: str, payload: dict[str, Any], md: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    base = out_dir / f"{stem}-{stamp}-{payload['git_sha']}"
    base.with_suffix(".json").write_text(
        json.dumps(payload, indent=2, default=_default), encoding="utf-8"
    )
    base.with_suffix(".md").write_text(md, encoding="utf-8")
    return base.with_suffix(".md")


def _md_routing(title: str, s: dict[str, Any]) -> list[str]:
    lines = [
        f"## {title}",
        "",
        f"- **False auto-approvals: {s['false_auto_approvals']}** (gate: 0) {s['false_auto_approval_ids'] or ''}",
        f"- Route accuracy: {s['route_accuracy']:.3f} · clean auto-approval rate: {s['clean_auto_approval_rate']:.3f}",
        f"- needs_review without a reason: {len(s['needs_review_without_reason'])}",
        f"- False flags on clean invoices: {len(s['false_flags_on_clean'])} {s['false_flags_on_clean'][:5]}",
        "",
        "| Anomaly | caught | total |",
        "|---|---|---|",
        *(f"| {k} | {v['caught']} | {v['total']} |" for k, v in s["anomaly_catch"].items()),
        "",
        "| expected -> routed | n |",
        "|---|---|",
        *(f"| {k} | {v} |" for k, v in s["confusion"].items()),
        "",
    ]
    if s["anomalies_missed"]:
        lines += [f"Missed: {', '.join(s['anomalies_missed'])}", ""]
    return lines


async def run_system(
    ds: InvoiceDataset,
    settings: LedgerSettings,
    llm: LLMClient,
    *,
    sample: int | None,
    max_cost: Decimal,
    prompt_version: str,
    oracle_mode: bool,
) -> tuple[list[Routed], ExtractStats, str | None]:
    """Sequential on purpose: per-invoice latency is measured without queueing effects."""
    policy = settings.policy()
    dups = DuplicateTracker()
    stats = ExtractStats()
    routed: list[Routed] = []
    stopped: str | None = None
    items = ds.items[:sample] if sample else ds.items
    for gt in items:
        if sum(stats.costs, Decimal(0)) >= max_cost:
            stopped = "max_cost"
            break
        try:
            pdf = extract_text(ds.pdf_bytes(gt), min_chars_per_page=settings.ocr_min_chars_per_page)
        except PdfUnreadable as e:
            routed.append(
                Routed(gt, "needs_review", pipeline_reason("UNREADABLE_PDF", str(e)).reasons)
            )
            continue
        if pdf.needs_ocr:
            routed.append(
                Routed(gt, "needs_review", pipeline_reason("NEEDS_OCR", "no text layer").reasons)
            )
            continue
        stats.n += 1
        started = time.perf_counter()
        try:
            if oracle_mode:
                outcome_ex, attempts, unsupported = oracle_extraction(gt), [], []
            else:
                out = await extract(
                    llm,
                    pdf.pages,
                    prompt_version=prompt_version,
                    max_attempts=settings.llm_max_attempts,
                )
                outcome_ex, attempts, unsupported = (
                    out.extraction,
                    out.attempts,
                    out.unsupported_fields,
                )
        except ExtractionInvalid as e:
            stats.invalid += 1
            stats.costs.append(sum((a.result.usage.cost_usd for a in e.attempts), Decimal(0)))
            routed.append(
                Routed(
                    gt,
                    "needs_review",
                    pipeline_reason("EXTRACTION_INVALID", "invalid twice").reasons,
                )
            )
            continue
        except LLMError as e:
            stats.errors.append(f"{gt.id}: {type(e).__name__}: {e}"[:200])
            routed.append(Routed(gt, "failed", []))
            continue
        stats.latencies_ms.append(int((time.perf_counter() - started) * 1000))
        stats.costs.append(sum((a.result.usage.cost_usd for a in attempts), Decimal(0)))
        if len(attempts) <= 1:
            stats.valid += 1
        else:
            stats.repaired += 1
        stats.unsupported.update(f.split(".")[-1] for f in unsupported)
        stats.add(gt, outcome_ex)
        d = decide_offline(
            outcome_ex,
            DocSignals(pdf.pages, pdf.hidden_chars),
            ds.reference,
            policy,
            gt,
            dups,
            ds.as_of,
        )
        routed.append(Routed(gt, d.routing.status, d.routing.reasons))
    return routed, stats, stopped


def _guard_heldout(args: argparse.Namespace, ds: InvoiceDataset, suite: str) -> int | None:
    if args.split != "test":
        return None
    if args.sample:
        print("refused: held-out sets run in full (no --sample)", file=sys.stderr)
        return 2
    try:
        check_heldout(Path(args.evals_dir) / "HELDOUT_LOG.md", suite, "test", ds.sha256, args.force)
    except HeldOutAlreadyRun as e:
        print(f"refused: {e}", file=sys.stderr)
        return 3
    return None


async def _main(args: argparse.Namespace) -> int:
    try:
        ds = load_invoices(args.split, Path(args.data))
    except DatasetError as e:
        print(f"dataset error: {e}", file=sys.stderr)
        return 2
    settings = get_ledger_settings()
    oracle_mode = args.cmd == "routing"
    suite = "ledgerlens-routing-oracle" if oracle_mode else "ledgerlens-system"
    if (refusal := _guard_heldout(args, ds, suite)) is not None:
        return refusal
    if not oracle_mode and settings.llm_provider == "fake":
        print(
            "refused: 'system' needs a real model (LLM_PROVIDER=openai_compat). Use 'routing' for the "
            "oracle ablation.",
            file=sys.stderr,
        )
        return 2

    llm = (
        oracle_llm(ds.items)
        if oracle_mode
        else build_llm_client(settings, load_pricing(settings.pricing_path))
    )
    prompt_version = args.prompt or settings.prompt_version
    routed, stats, stopped = await run_system(
        ds,
        settings,
        llm,
        sample=args.sample,
        max_cost=args.max_cost,
        prompt_version=prompt_version,
        oracle_mode=oracle_mode,
    )
    s = score(routed)
    sha = git_sha()
    payload = {
        "suite": suite,
        "split": args.split,
        "dataset_sha256": ds.sha256,
        "git_sha": sha,
        "model": "oracle (ground truth, not a model)" if oracle_mode else settings.llm_model,
        "prompt_version": None if oracle_mode else prompt_version,
        "stopped_reason": stopped,
        "routing": s,
        "extraction": None if oracle_mode else stats.summary(),
        "field_errors": [] if oracle_mode else stats.field_errors,
    }
    title = (
        "Routing on ground truth (oracle extraction)"
        if oracle_mode
        else f"System: {settings.llm_model} / {prompt_version}"
    )
    md = [
        f"# LedgerLens eval: {suite} / {args.split}",
        "",
        f"Dataset `{ds.sha256[:16]}…` · git `{sha}` · synthetic data: real-world accuracy will differ.",
        "",
    ]
    md += _md_routing(title, s)
    if not oracle_mode:
        ex_s = stats.summary()
        md += [
            "## Extraction",
            "",
            f"- Valid output rate: {ex_s['valid_rate']:.3f} (first try {ex_s['valid_first_try']}, after repair "
            f"{ex_s['valid_after_repair']}, invalid {ex_s['invalid']})",
            f"- Line items: P {ex_s['line_precision']:.3f} · R {ex_s['line_recall']:.3f} · F1 {ex_s['line_f1']:.3f}",
            f"- Cost: total ${ex_s['cost_usd_total']} · mean ${ex_s['cost_usd_mean']} · p95 ${ex_s['cost_usd_p95']}",
            f"- Latency: p50 {ex_s['latency_ms_p50']} ms · p95 {ex_s['latency_ms_p95']} ms",
            "",
            "| Field | accuracy |",
            "|---|---|",
            *(f"| {k} | {v:.3f} |" for k, v in ex_s["field_accuracy"].items()),
            "",
        ]
    path = _write(
        Path(args.evals_dir) / "reports", f"{suite}-{args.split}", payload, "\n".join(md) + "\n"
    )
    if args.split == "test":
        record_heldout(
            Path(args.evals_dir) / "HELDOUT_LOG.md", suite, "test", ds.sha256, sha, args.force
        )
    print("\n".join(md))
    print(f"wrote {path}")
    return 0 if s["false_auto_approvals"] == 0 and stopped is None else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ledgerlens.evals")
    p.add_argument("cmd", choices=["routing", "system"])
    p.add_argument("--split", default="dev", choices=["dev", "test"])
    p.add_argument("--sample", type=int, default=None, help="first N invoices (dev only)")
    p.add_argument("--max-cost", type=Decimal, default=Decimal("2.00"), help="USD cap for this run")
    p.add_argument("--prompt", default=None, help="prompt version, e.g. extract_v2")
    p.add_argument("--force", action="store_true", help="allow a logged re-run of the held-out set")
    p.add_argument("--data", default=str(DEFAULT_DIR))
    p.add_argument("--evals-dir", default="evals")
    return asyncio.run(_main(p.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
