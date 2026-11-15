"""Database access for LedgerLens. Functions take a session and never commit, except where
the name says so; the service layer owns transaction boundaries."""

from __future__ import annotations

import secrets
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ledgerlens.adapters.db.models import (
    AuditEventRow,
    ExtractionRunRow,
    GoodsReceiptRow,
    InvoiceFileRow,
    InvoiceLineRow,
    InvoiceRow,
    POLineRow,
    PurchaseOrderRow,
    ReviewDecisionRow,
    ValidationIssueRow,
    VendorRow,
)
from ledgerlens.domain.decide import Decision
from ledgerlens.domain.normalize import norm_id, show
from ledgerlens.domain.reference import POLine, PurchaseOrder, Reference, ReferenceSet, Vendor
from ledgerlens.domain.rules import Issue
from ledgerlens.domain.schemas import InvoiceExtraction
from ledgerlens.domain.states import assert_transition
from ledgerlens.services.extract import Attempt


@asynccontextmanager
async def atomic(session: AsyncSession) -> AsyncIterator[None]:
    """Commit on success, roll back on error. Works on a request session that already ran
    a read (the auth lookup), where ``session.begin()`` would raise."""
    try:
        yield
    except BaseException:
        await session.rollback()
        raise
    await session.commit()


def new_invoice_id() -> str:
    return f"inv_{secrets.token_hex(10)}"


def job_key(invoice_id: str) -> str:
    return f"invoice:{invoice_id}"


# ------------------------------------------------------------------ audit + status


async def audit(
    session: AsyncSession,
    entity: str,
    entity_id: str,
    action: str,
    actor: str,
    payload: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEventRow(
            entity=entity, entity_id=entity_id, action=action, actor=actor, payload=payload or {}
        )
    )


async def lock_invoice(session: AsyncSession, invoice_id: str) -> InvoiceRow | None:
    row: InvoiceRow | None = await session.scalar(
        select(InvoiceRow).where(InvoiceRow.id == invoice_id).with_for_update()
    )
    return row


async def set_status(
    session: AsyncSession,
    inv: InvoiceRow,
    new: str,
    actor: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """The only way a status changes: validated against the state machine, always audited."""
    old = inv.status
    assert_transition(old, new)
    inv.status = new
    await audit(
        session, "invoice", inv.id, f"status:{new}", actor, {"from": old, **(payload or {})}
    )


# ------------------------------------------------------------------ upload


async def insert_upload(
    session: AsyncSession, *, file_sha256: str, content: bytes, filename: str | None, actor: str
) -> tuple[str, bool]:
    """Idempotent by file hash. Returns (invoice_id, created). Does not commit."""
    invoice_id = new_invoice_id()
    stmt = (
        insert(InvoiceRow)
        .values(
            id=invoice_id,
            file_sha256=file_sha256,
            filename=filename,
            uploaded_by=actor,
            status="queued",
        )
        .on_conflict_do_nothing(index_elements=["file_sha256"])
        .returning(InvoiceRow.id)
    )
    created_id = await session.scalar(stmt)
    if created_id is None:
        existing = await session.scalar(
            select(InvoiceRow.id).where(InvoiceRow.file_sha256 == file_sha256)
        )
        if existing is None:
            raise RuntimeError("upload conflict without an existing row")
        return existing, False
    session.add(InvoiceFileRow(invoice_id=invoice_id, content=content, size=len(content)))
    await audit(
        session,
        "invoice",
        invoice_id,
        "uploaded",
        actor,
        {"sha256": file_sha256, "bytes": len(content)},
    )
    return invoice_id, True


async def file_content(session: AsyncSession, invoice_id: str) -> bytes:
    content = await session.scalar(
        select(InvoiceFileRow.content).where(InvoiceFileRow.invoice_id == invoice_id)
    )
    if content is None:
        raise LookupError(f"no file for {invoice_id}")
    return content


async def job_id_for(session: AsyncSession, invoice_id: str) -> str | None:
    jid = await session.scalar(
        text("SELECT id FROM jobs WHERE idempotency_key = :k"), {"k": job_key(invoice_id)}
    )
    return str(jid) if jid is not None else None


# ------------------------------------------------------------------ reference data


async def load_reference(session: AsyncSession, po_number: str | None) -> Reference:
    """All vendors (hundreds at most) plus the one PO the invoice cites, with received qty."""
    vendors = [
        Vendor(
            id=v.id,
            name=v.name,
            tax_id=v.tax_id,
            default_currency=v.default_currency,
            tax_rate=v.tax_rate,
        )
        for v in (await session.scalars(select(VendorRow))).all()
    ]
    ref = Reference(vendors=vendors)
    if not po_number:
        return ref
    rows = (await session.scalars(select(PurchaseOrderRow))).all()
    po_row = next((p for p in rows if norm_id(p.po_number) == norm_id(po_number)), None)
    if po_row is None:
        return ref
    received = (
        select(
            GoodsReceiptRow.po_line_id,
            func.coalesce(func.sum(GoodsReceiptRow.qty_received), 0).label("q"),
        )
        .group_by(GoodsReceiptRow.po_line_id)
        .subquery()
    )
    line_rows = (
        await session.execute(
            select(POLineRow, func.coalesce(received.c.q, 0))
            .outerjoin(received, received.c.po_line_id == POLineRow.id)
            .where(POLineRow.po_id == po_row.id)
            .order_by(POLineRow.line_no)
        )
    ).all()
    lines = tuple(
        POLine(
            line_no=pl.line_no,
            sku=pl.sku,
            description=pl.description,
            qty=pl.qty,
            unit_price=pl.unit_price,
            qty_received=Decimal(q),
        )
        for pl, q in line_rows
    )
    ref.pos[norm_id(po_row.po_number)] = PurchaseOrder(
        po_number=po_row.po_number,
        vendor_id=po_row.vendor_id,
        currency=po_row.currency,
        lines=lines,
    )
    return ref


async def seed_reference(session: AsyncSession, ref: ReferenceSet) -> dict[str, int]:
    """Load vendors, POs and goods receipts (idempotent: skips existing vendors/POs). Commits."""
    have_vendors = set((await session.scalars(select(VendorRow.id))).all())
    have_pos = set((await session.scalars(select(PurchaseOrderRow.po_number))).all())
    added: defaultdict[str, int] = defaultdict(int)
    for v in ref.vendors:
        if v.id not in have_vendors:
            session.add(
                VendorRow(
                    id=v.id,
                    name=v.name,
                    tax_id=v.tax_id,
                    default_currency=v.default_currency,
                    tax_rate=v.tax_rate,
                )
            )
            added["vendors"] += 1
    await session.flush()
    for po in ref.purchase_orders:
        if po.po_number in have_pos:
            continue
        row = PurchaseOrderRow(po_number=po.po_number, vendor_id=po.vendor_id, currency=po.currency)
        session.add(row)
        await session.flush()
        for pl in po.lines:
            line = POLineRow(
                po_id=row.id,
                line_no=pl.line_no,
                sku=pl.sku,
                description=pl.description,
                qty=pl.qty,
                unit_price=pl.unit_price,
            )
            session.add(line)
            await session.flush()
            if pl.qty_received > 0:
                session.add(GoodsReceiptRow(po_line_id=line.id, qty_received=pl.qty_received))
                added["goods_receipts"] += 1
        added["purchase_orders"] += 1
    await session.commit()
    return dict(added)


# ------------------------------------------------------------------ processing results


async def save_runs(
    session: AsyncSession, invoice_id: str, attempts: list[Attempt], prompt_version: str
) -> None:
    for n, a in enumerate(attempts, start=1):
        r = a.result
        session.add(
            ExtractionRunRow(
                invoice_id=invoice_id,
                model=r.model,
                prompt_version=prompt_version,
                tokens_in=r.usage.tokens_in,
                tokens_out=r.usage.tokens_out,
                cost_usd=r.usage.cost_usd,
                latency_ms=r.latency_ms,
                attempt=n,
                valid=a.valid,
                raw_output=r.parsed,
                error=a.error,
            )
        )


async def claim_dedupe_key(session: AsyncSession, inv: InvoiceRow, key: str | None) -> str | None:
    """Try to own ``key``. Returns the id of the invoice that already owns it (a duplicate),
    or None. The unique index makes this race-safe across workers."""
    if key is None:
        inv.dedupe_key = None
        return None
    owner = await session.scalar(
        select(InvoiceRow.id).where(InvoiceRow.dedupe_key == key, InvoiceRow.id != inv.id)
    )
    if owner is not None:
        return owner
    try:
        async with session.begin_nested():
            inv.dedupe_key = key
            await session.flush()
    except IntegrityError:
        inv.dedupe_key = None
        winner: str | None = await session.scalar(
            select(InvoiceRow.id).where(InvoiceRow.dedupe_key == key)
        )
        return winner
    return None


async def replace_results(
    session: AsyncSession,
    inv: InvoiceRow,
    ex: InvoiceExtraction | None,
    decision: Decision | None,
    issues: list[Issue],
    *,
    page_count: int | None,
) -> None:
    """Overwrite derived data for a (re)processed invoice. Idempotent by invoice id."""
    await session.execute(delete(InvoiceLineRow).where(InvoiceLineRow.invoice_id == inv.id))
    await session.execute(delete(ValidationIssueRow).where(ValidationIssueRow.invoice_id == inv.id))
    inv.page_count = page_count
    inv.reasons = [i.as_dict() for i in (decision.routing.reasons if decision else issues)]
    for i in issues:
        session.add(
            ValidationIssueRow(
                invoice_id=inv.id,
                code=i.code,
                severity=i.severity,
                field=i.field,
                message=i.message,
                line=i.line,
            )
        )
    if ex is None or decision is None:
        return
    v = decision.values
    inv.extraction = ex.model_dump(mode="json")
    inv.vendor_id = decision.vendor.vendor.id if decision.vendor.vendor else None
    inv.invoice_number, inv.invoice_date, inv.due_date = (
        v.invoice_number,
        v.invoice_date,
        v.due_date,
    )
    inv.po_number, inv.currency = v.po_number, v.currency
    inv.subtotal, inv.tax, inv.total = v.subtotal, v.tax, v.total
    po_line = {m.invoice_line: m.po_line_no for m in decision.match.lines}
    for n, ln in enumerate(v.lines, start=1):
        session.add(
            InvoiceLineRow(
                invoice_id=inv.id,
                line_no=n,
                description=ln.description,
                qty=ln.quantity,
                unit_price=ln.unit_price,
                amount=ln.amount,
                po_line_ref=ln.po_line_ref,
                po_line_no=po_line.get(n),
            )
        )


# ------------------------------------------------------------------ reads


def _money(x: Decimal | None) -> str | None:
    return None if x is None else f"{x:.2f}"


async def invoice_view(session: AsyncSession, invoice_id: str) -> dict[str, Any] | None:
    inv = await session.get(InvoiceRow, invoice_id)
    if inv is None:
        return None
    lines = (
        await session.scalars(
            select(InvoiceLineRow)
            .where(InvoiceLineRow.invoice_id == invoice_id)
            .order_by(InvoiceLineRow.line_no)
        )
    ).all()
    runs = (
        await session.scalars(
            select(ExtractionRunRow)
            .where(ExtractionRunRow.invoice_id == invoice_id)
            .order_by(ExtractionRunRow.id)
        )
    ).all()
    decisions = (
        await session.scalars(
            select(ReviewDecisionRow)
            .where(ReviewDecisionRow.invoice_id == invoice_id)
            .order_by(ReviewDecisionRow.id)
        )
    ).all()
    history = (
        await session.scalars(
            select(AuditEventRow)
            .where(AuditEventRow.entity_id == invoice_id)
            .order_by(AuditEventRow.id)
        )
    ).all()
    last = runs[-1] if runs else None
    return {
        "invoice_id": inv.id,
        "status": inv.status,
        "job_id": await job_id_for(session, inv.id),
        "reasons": inv.reasons,
        "vendor_id": inv.vendor_id,
        "invoice_number": inv.invoice_number,
        "invoice_date": inv.invoice_date,
        "due_date": inv.due_date,
        "po_number": inv.po_number,
        "duplicate_of": inv.duplicate_of,
        "totals": {
            "subtotal": _money(inv.subtotal),
            "tax": _money(inv.tax),
            "total": _money(inv.total),
            "currency": inv.currency,
        },
        "lines": [
            {
                "line_no": li.line_no,
                "description": li.description,
                "qty": show(li.qty),
                "unit_price": show(li.unit_price, 2),
                "amount": _money(li.amount),
                "po_line_ref": li.po_line_ref,
                "po_line_no": li.po_line_no,
            }
            for li in lines
        ],
        "extraction": None
        if last is None
        else {
            "run_id": last.id,
            "model": last.model,
            "prompt_version": last.prompt_version,
            "attempts": len(runs),
            "cost_usd": str(sum((r.cost_usd for r in runs), Decimal(0))),
            "latency_ms": sum(r.latency_ms for r in runs),
            "valid": last.valid,
        },
        "decisions": [
            {
                "decision": d.decision,
                "actor_id": d.actor_id,
                "role": d.role,
                "reason": d.reason,
                "corrections": d.corrections,
                "at": d.created_at,
            }
            for d in decisions
        ],
        "history": [
            {"action": h.action, "actor": h.actor, "payload": h.payload, "at": h.created_at}
            for h in history
        ],
        "created_at": inv.created_at,
        "updated_at": inv.updated_at,
    }


async def review_queue(
    session: AsyncSession, status: str, limit: int, after: str | None
) -> tuple[list[dict[str, Any]], str | None]:
    """Keyset pagination on (created_at, id): stable under concurrent inserts."""
    q = select(InvoiceRow).where(InvoiceRow.status == status)
    if after:
        anchor = await session.get(InvoiceRow, after)
        if anchor is not None:
            q = q.where(
                (InvoiceRow.created_at > anchor.created_at)
                | ((InvoiceRow.created_at == anchor.created_at) & (InvoiceRow.id > anchor.id))
            )
    rows = (
        await session.scalars(q.order_by(InvoiceRow.created_at, InvoiceRow.id).limit(limit + 1))
    ).all()
    page = rows[:limit]
    items = [
        {
            "invoice_id": r.id,
            "status": r.status,
            "invoice_number": r.invoice_number,
            "vendor_id": r.vendor_id,
            "total": _money(r.total),
            "currency": r.currency,
            "reasons": r.reasons,
            "created_at": r.created_at,
        }
        for r in page
    ]
    return items, (page[-1].id if len(rows) > limit else None)


async def add_decision(
    session: AsyncSession,
    invoice_id: str,
    actor_id: str,
    role: str,
    decision: str,
    reason: str,
    corrections: dict[str, Any] | None,
) -> None:
    session.add(
        ReviewDecisionRow(
            invoice_id=invoice_id,
            actor_id=actor_id,
            role=role,
            decision=decision,
            reason=reason,
            corrections=corrections,
        )
    )
