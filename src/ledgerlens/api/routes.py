"""LedgerLens HTTP API."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.adapters.db import jobs
from app.api.auth import Principal, require_role
from app.api.deps import SessionDep
from app.api.errors import DomainError
from ledgerlens.adapters.db import repo
from ledgerlens.api.ratelimit import SlidingWindowLimiter
from ledgerlens.config import LedgerSettings
from ledgerlens.services import intake, review

router = APIRouter(prefix="/v1", tags=["invoices"])
admin = APIRouter(prefix="/v1/admin", tags=["admin"])

Clerk = Annotated[Principal, Depends(require_role("clerk"))]
Admin = Annotated[Principal, Depends(require_role("admin"))]


def ledger_settings(request: Request) -> LedgerSettings:
    return cast(LedgerSettings, request.app.state.settings)


def limiter(request: Request) -> SlidingWindowLimiter:
    return cast(SlidingWindowLimiter, request.app.state.upload_limiter)


SettingsDep = Annotated[LedgerSettings, Depends(ledger_settings)]


class UploadResponse(BaseModel):
    invoice_id: str
    job_id: str | None
    status: str
    duplicate_of: str | None


@router.post("/invoices", status_code=status.HTTP_202_ACCEPTED)
async def upload_invoice(
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    p: Clerk,
    file: Annotated[UploadFile, File(description="Invoice PDF, at most 10 MB")],
) -> UploadResponse:
    """Accepted for asynchronous processing: poll GET /v1/invoices/{id}. Uploading the same
    file again returns the same invoice."""
    limiter(request).check(p.key_id)
    data = await file.read(settings.max_upload_bytes + 1)
    res = await intake.upload(
        session,
        data=data,
        filename=file.filename,
        actor=p.key_id,
        max_bytes=settings.max_upload_bytes,
        job_max_attempts=settings.job_max_attempts,
    )
    response.headers["Location"] = f"/v1/invoices/{res.invoice_id}"
    return UploadResponse(
        invoice_id=res.invoice_id,
        job_id=res.job_id,
        status=res.status,
        duplicate_of=res.duplicate_of,
    )


@router.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: str, session: SessionDep, _: Clerk) -> dict[str, Any]:
    view = await repo.invoice_view(session, invoice_id)
    if view is None:
        raise DomainError(404, "not-found", "Invoice not found")
    return view


class QueuePage(BaseModel):
    items: list[dict[str, Any]]
    next_after: str | None


@router.get("/review-queue")
async def review_queue(
    session: SessionDep,
    _: Clerk,
    status_filter: Annotated[
        Literal["needs_review", "auto_approved", "failed", "rejected", "approved"],
        Query(alias="status"),
    ] = "needs_review",
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    after: Annotated[str | None, Query(max_length=32)] = None,
) -> QueuePage:
    items, nxt = await repo.review_queue(session, status_filter, limit, after)
    return QueuePage(items=items, next_after=nxt)


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: review.DecisionKind
    reason: str = Field(min_length=3, max_length=1000)
    corrections: dict[str, str] | None = None


class DecisionResponse(BaseModel):
    invoice_id: str
    status: str


@router.post("/invoices/{invoice_id}/decision")
async def decide(
    invoice_id: str, body: DecisionRequest, session: SessionDep, settings: SettingsDep, p: Clerk
) -> DecisionResponse:
    new = await review.decide_invoice(
        session, p, invoice_id, body.decision, body.reason, body.corrections, settings.policy()
    )
    return DecisionResponse(invoice_id=invoice_id, status=new)


@router.get("/jobs/{job_id}")
async def get_job(job_id: uuid.UUID, session: SessionDep, _: Clerk) -> dict[str, Any]:
    job = await jobs.get_job(session, job_id)
    if job is None:
        raise DomainError(404, "not-found", "Job not found")
    return job


@admin.post("/jobs/{job_id}/requeue")
async def requeue_job(job_id: uuid.UUID, session: SessionDep, p: Admin) -> dict[str, Any]:
    """Give a dead-lettered job fresh attempts and put its invoice back in the queue."""
    job = await jobs.get_job(session, job_id)
    if job is None:
        raise DomainError(404, "not-found", "Job not found")
    if job["status"] != "dead":
        raise DomainError(409, "conflict", "Job is not dead-lettered", f"status is {job['status']}")
    invoice_id = await session.scalar(
        text("SELECT payload->>'invoice_id' FROM jobs WHERE id = :id"), {"id": job_id}
    )
    if invoice_id:
        inv = await repo.lock_invoice(session, invoice_id)
        if inv is not None and inv.status == "processing":  # worker died on its last attempt
            await repo.set_status(session, inv, "failed", p.key_id, {"action": "requeue"})
        if inv is not None and inv.status == "failed":
            await repo.set_status(session, inv, "queued", p.key_id, {"action": "requeue"})
    ok = await jobs.requeue(session, job_id)  # commits the invoice change and the job together
    return {"job_id": str(job_id), "requeued": ok, "invoice_id": invoice_id}


@router.get("/whoami")
async def whoami(p: Annotated[Principal, Depends(require_role("viewer"))]) -> dict[str, str]:
    return {"key_id": p.key_id, "role": p.role}
