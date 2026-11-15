"""The processing job: PDF text -> LLM extraction -> rules -> 3-way match -> routing.

A deterministic state machine with one LLM step. Safe to run more than once for the same
invoice (at-least-once delivery): finished invoices are skipped, and a restarted run
replaces the derived rows instead of adding to them.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

import structlog
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.db.jobs import ClaimedJob
from app.adapters.llm import LLMClient
from app.main import AppContext
from app.worker import Handler
from ledgerlens.adapters.db import repo
from ledgerlens.adapters.pdf import PdfText, PdfUnreadable, extract_text_async
from ledgerlens.config import LedgerSettings
from ledgerlens.domain.decide import DocSignals, decide, dedupe_key, resolve_vendor
from ledgerlens.domain.rules import Issue
from ledgerlens.domain.states import TERMINAL_AUTOMATED
from ledgerlens.observability import EXTRACTIONS, INVOICES, STAGE_SECONDS
from ledgerlens.services.extract import ExtractionInvalid, extract

log = structlog.get_logger()
tracer = trace.get_tracer("ledgerlens.pipeline")
SYSTEM = "system"
DONE = TERMINAL_AUTOMATED | {"approved"}


@contextmanager
def stage(name: str) -> Iterator[None]:
    started = time.perf_counter()
    with tracer.start_as_current_span(f"pipeline.{name}"):
        try:
            yield
        finally:
            STAGE_SECONDS.labels(stage=name).observe(time.perf_counter() - started)


class Pipeline:
    def __init__(
        self, sm: async_sessionmaker[AsyncSession], llm: LLMClient, settings: LedgerSettings
    ) -> None:
        self.sm, self.llm, self.settings = sm, llm, settings
        self.policy = settings.policy()

    async def _finish_without_extraction(
        self, invoice_id: str, issue: Issue, pdf: PdfText | None
    ) -> str:
        async with self.sm() as s, s.begin():
            inv = await repo.lock_invoice(s, invoice_id)
            if inv is None:
                raise LookupError(invoice_id)
            await repo.replace_results(
                s, inv, None, None, [issue], page_count=pdf.page_count if pdf else None
            )
            await repo.set_status(s, inv, "needs_review", SYSTEM, {"reasons": [issue.code]})
        INVOICES.labels(status="needs_review").inc()
        return "needs_review"

    async def process(self, invoice_id: str) -> str:
        """Returns the invoice's status after this run."""
        bound = log.bind(invoice_id=invoice_id)
        async with self.sm() as s, s.begin():
            inv = await repo.lock_invoice(s, invoice_id)
            if inv is None:
                raise LookupError(f"invoice {invoice_id} not found")
            if inv.status in DONE:
                bound.info("already_processed", status=inv.status)
                return inv.status
            await repo.set_status(s, inv, "processing", SYSTEM)
            content = await repo.file_content(s, invoice_id)

        with stage("text"):
            try:
                pdf = await extract_text_async(
                    content,
                    timeout_s=self.settings.pdf_parse_timeout_s,
                    min_chars_per_page=self.settings.ocr_min_chars_per_page,
                )
            except PdfUnreadable as e:
                return await self._finish_without_extraction(
                    invoice_id, Issue("UNREADABLE_PDF", "error", "document", str(e)), None
                )
        if pdf.needs_ocr:  # never send empty text to the model
            return await self._finish_without_extraction(
                invoice_id,
                Issue(
                    "NEEDS_OCR",
                    "error",
                    "document",
                    f"text layer too thin: {pdf.char_counts} chars/page",
                ),
                pdf,
            )

        with stage("extract"):
            try:
                outcome = await extract(
                    self.llm,
                    pdf.pages,
                    prompt_version=self.settings.prompt_version,
                    max_attempts=self.settings.llm_max_attempts,
                )
            except ExtractionInvalid as e:
                async with self.sm() as s, s.begin():
                    await repo.save_runs(s, invoice_id, e.attempts, self.settings.prompt_version)
                EXTRACTIONS.labels(valid="false").inc(len(e.attempts))
                return await self._finish_without_extraction(
                    invoice_id,
                    Issue(
                        "EXTRACTION_INVALID",
                        "error",
                        "document",
                        "model output failed schema validation twice",
                    ),
                    pdf,
                )
        for a in outcome.attempts:
            EXTRACTIONS.labels(valid=str(a.valid).lower()).inc()

        with stage("decide"):
            async with self.sm() as s, s.begin():
                await repo.save_runs(s, invoice_id, outcome.attempts, self.settings.prompt_version)
                inv = await repo.lock_invoice(s, invoice_id)
                if inv is None:
                    raise LookupError(invoice_id)
                ex = outcome.extraction
                ref = await repo.load_reference(s, ex.po_number.value)
                vendor = resolve_vendor(ex, ref, self.policy)
                key = dedupe_key(
                    vendor.vendor.id if vendor.vendor else None,
                    ex.invoice_number.value,
                    ex.total.value,
                )
                owner = await repo.claim_dedupe_key(s, inv, key)
                inv.duplicate_of = owner
                decision = decide(
                    ex,
                    DocSignals(pdf.pages, pdf.hidden_chars),
                    ref,
                    self.policy,
                    self.settings.today(),
                    vendor=vendor,
                    is_duplicate=owner is not None,
                )
                await repo.replace_results(
                    s, inv, ex, decision, decision.issues, page_count=pdf.page_count
                )
                status = decision.routing.status
                await repo.set_status(
                    s,
                    inv,
                    status,
                    SYSTEM,
                    {
                        "reasons": [r.code for r in decision.routing.reasons],
                        "unsupported_fields": outcome.unsupported_fields,
                    },
                )
        INVOICES.labels(status=status).inc()
        bound.info(
            "invoice_routed", status=status, reasons=[r.code for r in decision.routing.reasons]
        )
        return status

    async def mark_failed(self, invoice_id: str, error: str) -> None:
        async with self.sm() as s, s.begin():
            inv = await repo.lock_invoice(s, invoice_id)
            if inv is not None and inv.status in ("queued", "processing"):
                if inv.status == "queued":
                    await repo.set_status(s, inv, "processing", SYSTEM)
                await repo.set_status(s, inv, "failed", SYSTEM, {"error": error[:500]})
        INVOICES.labels(status="failed").inc()


def build_handlers(ctx: AppContext) -> Mapping[str, Handler]:
    settings = ctx.settings
    if not isinstance(settings, LedgerSettings):
        raise TypeError("LedgerLens handlers need LedgerSettings")
    pipeline = Pipeline(ctx.sessionmaker, ctx.llm, settings)

    async def process_invoice(job: ClaimedJob) -> None:
        invoice_id = str(job.payload["invoice_id"])
        with tracer.start_as_current_span("job.process_invoice") as span:
            span.set_attribute("invoice.id", invoice_id)
            span.set_attribute("job.attempt", job.attempts)
            try:
                await pipeline.process(invoice_id)
            except Exception as e:
                if job.attempts >= job.max_attempts:  # last try: dead-letter the invoice too
                    await pipeline.mark_failed(invoice_id, f"{type(e).__name__}: {e}")
                raise

    return {"process_invoice": process_invoice}
