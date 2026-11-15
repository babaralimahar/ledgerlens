"""LedgerLens-specific metrics."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

INVOICES = Counter("invoices_routed_total", "Invoices by final automated status", ["status"])
EXTRACTIONS = Counter("extraction_attempts_total", "LLM extraction attempts", ["valid"])
STAGE_SECONDS = Histogram(
    "pipeline_stage_seconds",
    "Time per pipeline stage",
    ["stage"],
    buckets=(0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60),
)
UPLOADS = Counter("invoice_uploads_total", "Uploads", ["outcome"])
