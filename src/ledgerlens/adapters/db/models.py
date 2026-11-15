"""LedgerLens tables. They share the template's metadata so Alembic sees one schema."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters.db.models import Base

INVOICE_STATUSES = (
    "queued",
    "processing",
    "auto_approved",
    "needs_review",
    "approved",
    "rejected",
    "failed",
)


class VendorRow(Base):
    __tablename__ = "vendors"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    tax_id: Mapped[str] = mapped_column(String(64), unique=True)
    default_currency: Mapped[str] = mapped_column(String(3))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PurchaseOrderRow(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    po_number: Mapped[str] = mapped_column(String(64), unique=True)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("vendors.id"))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(16), server_default="open")


class POLineRow(Base):
    __tablename__ = "po_lines"
    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id", ondelete="CASCADE"))
    line_no: Mapped[int] = mapped_column(Integer)
    sku: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(300))
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 4))


class GoodsReceiptRow(Base):
    __tablename__ = "goods_receipts"
    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    po_line_id: Mapped[int] = mapped_column(ForeignKey("po_lines.id", ondelete="CASCADE"))
    qty_received: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class InvoiceRow(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint(f"status IN {INVOICE_STATUSES!r}", name="ck_invoices_status"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    vendor_id: Mapped[str | None] = mapped_column(ForeignKey("vendors.id"))
    invoice_number: Mapped[str | None] = mapped_column(String(64))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    po_number: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str | None] = mapped_column(String(3))
    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    tax: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    status: Mapped[str] = mapped_column(String(16), server_default="queued")
    reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
    extraction: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    file_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    filename: Mapped[str | None] = mapped_column(String(255))
    page_count: Mapped[int | None] = mapped_column(Integer)
    dedupe_key: Mapped[str | None] = mapped_column(Text, unique=True)
    duplicate_of: Mapped[str | None] = mapped_column(String(32))
    uploaded_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class InvoiceFileRow(Base):
    """File bytes in Postgres for the demo; a real deployment uses S3-compatible storage."""

    __tablename__ = "invoice_files"
    invoice_id: Mapped[str] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), primary_key=True
    )
    content: Mapped[bytes] = mapped_column(LargeBinary)
    size: Mapped[int] = mapped_column(Integer)


class InvoiceLineRow(Base):
    __tablename__ = "invoice_lines"
    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    invoice_id: Mapped[str] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    line_no: Mapped[int] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(String(300))
    qty: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    po_line_ref: Mapped[str | None] = mapped_column(String(64))
    po_line_no: Mapped[int | None] = mapped_column(Integer)


class ExtractionRunRow(Base):
    __tablename__ = "extraction_runs"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    invoice_id: Mapped[str] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(40))
    tokens_in: Mapped[int] = mapped_column(Integer)
    tokens_out: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6))
    latency_ms: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer)
    valid: Mapped[bool] = mapped_column(Boolean)
    raw_output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ValidationIssueRow(Base):
    __tablename__ = "validation_issues"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    invoice_id: Mapped[str] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))
    field: Mapped[str] = mapped_column(String(80))
    message: Mapped[str] = mapped_column(Text)
    line: Mapped[int | None] = mapped_column(Integer)


class ReviewDecisionRow(Base):
    __tablename__ = "review_decisions"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    invoice_id: Mapped[str] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[str] = mapped_column(String(32))
    role: Mapped[str] = mapped_column(String(20))
    decision: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    corrections: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEventRow(Base):
    """Insert-only (enforced by a trigger in migration 0002)."""

    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    entity: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
