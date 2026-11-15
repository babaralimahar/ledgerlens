"""Upload: validate, deduplicate by file hash, store, enqueue. One transaction."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.db import jobs
from app.api.errors import DomainError
from ledgerlens.adapters.db import repo
from ledgerlens.adapters.db.models import InvoiceRow
from ledgerlens.adapters.pdf import is_pdf
from ledgerlens.observability import UPLOADS


@dataclass(frozen=True)
class UploadResult:
    invoice_id: str
    job_id: str | None
    status: str
    created: bool
    duplicate_of: str | None


async def upload(
    session: AsyncSession,
    *,
    data: bytes,
    filename: str | None,
    actor: str,
    max_bytes: int,
    job_max_attempts: int,
) -> UploadResult:
    if len(data) > max_bytes:
        UPLOADS.labels(outcome="too_large").inc()
        raise DomainError(413, "payload-too-large", "File too large", f"limit is {max_bytes} bytes")
    if not data:
        UPLOADS.labels(outcome="empty").inc()
        raise DomainError(422, "empty-file", "Empty file")
    if not is_pdf(data):
        UPLOADS.labels(outcome="not_pdf").inc()
        raise DomainError(
            415, "unsupported-media-type", "Not a PDF", "file does not start with %PDF-"
        )

    sha = hashlib.sha256(data).hexdigest()
    invoice_id, created = await repo.insert_upload(
        session, file_sha256=sha, content=data, filename=(filename or "")[:255] or None, actor=actor
    )
    if created:
        # enqueue commits the session: the invoice, its file, the audit event and the job
        # land together or not at all.
        job_id, _ = await jobs.enqueue(
            session,
            "process_invoice",
            {"invoice_id": invoice_id},
            idempotency_key=repo.job_key(invoice_id),
            max_attempts=job_max_attempts,
        )
        UPLOADS.labels(outcome="accepted").inc()
        return UploadResult(invoice_id, str(job_id), "queued", True, None)

    await session.rollback()
    row = (
        await session.execute(
            select(InvoiceRow.status, InvoiceRow.duplicate_of).where(InvoiceRow.id == invoice_id)
        )
    ).one()
    UPLOADS.labels(outcome="same_file").inc()
    return UploadResult(
        invoice_id, await repo.job_id_for(session, invoice_id), row.status, False, row.duplicate_of
    )
