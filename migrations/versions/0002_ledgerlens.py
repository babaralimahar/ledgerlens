"""ledgerlens: reference data, invoices, extraction runs, issues, decisions, insert-only audit

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = (
    "('queued', 'processing', 'auto_approved', 'needs_review', 'approved', 'rejected', 'failed')"
)


def _ts(name: str = "created_at") -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    op.create_table(
        "vendors",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("tax_id", sa.String(64), nullable=False, unique=True),
        sa.Column("default_currency", sa.String(3), nullable=False),
        sa.Column("tax_rate", sa.Numeric(6, 4), nullable=False),
        _ts(),
    )
    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column("po_number", sa.String(64), nullable=False, unique=True),
        sa.Column("vendor_id", sa.String(32), sa.ForeignKey("vendors.id"), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
    )
    op.create_table(
        "po_lines",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column(
            "po_id",
            sa.Integer,
            sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("line_no", sa.Integer, nullable=False),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("qty", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit_price", sa.Numeric(14, 4), nullable=False),
        sa.UniqueConstraint("po_id", "line_no", name="uq_po_lines_po_line_no"),
    )
    op.create_table(
        "goods_receipts",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column(
            "po_line_id",
            sa.Integer,
            sa.ForeignKey("po_lines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("qty_received", sa.Numeric(12, 3), nullable=False),
        _ts("received_at"),
    )

    op.create_table(
        "invoices",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("vendor_id", sa.String(32), sa.ForeignKey("vendors.id")),
        sa.Column("invoice_number", sa.String(64)),
        sa.Column("invoice_date", sa.Date),
        sa.Column("due_date", sa.Date),
        sa.Column("po_number", sa.String(64)),
        sa.Column("currency", sa.String(3)),
        sa.Column("subtotal", sa.Numeric(14, 2)),
        sa.Column("tax", sa.Numeric(14, 2)),
        sa.Column("total", sa.Numeric(14, 2)),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column(
            "reasons", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("extraction", postgresql.JSONB),
        sa.Column("file_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("filename", sa.String(255)),
        sa.Column("page_count", sa.Integer),
        sa.Column("dedupe_key", sa.Text, unique=True),
        sa.Column("duplicate_of", sa.String(32)),
        sa.Column("uploaded_by", sa.String(32), nullable=False),
        _ts(),
        _ts("updated_at"),
        sa.CheckConstraint(f"status IN {STATUSES}", name="ck_invoices_status"),
    )
    op.create_index("ix_invoices_status_created", "invoices", ["status", "created_at"])
    op.create_table(
        "invoice_files",
        sa.Column(
            "invoice_id",
            sa.String(32),
            sa.ForeignKey("invoices.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("content", sa.LargeBinary, nullable=False),
        sa.Column("size", sa.Integer, nullable=False),
    )
    op.create_table(
        "invoice_lines",
        sa.Column("id", sa.Integer, sa.Identity(), primary_key=True),
        sa.Column(
            "invoice_id",
            sa.String(32),
            sa.ForeignKey("invoices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("line_no", sa.Integer, nullable=False),
        sa.Column("description", sa.String(300)),
        sa.Column("qty", sa.Numeric(12, 3)),
        sa.Column("unit_price", sa.Numeric(14, 4)),
        sa.Column("amount", sa.Numeric(14, 2)),
        sa.Column("po_line_ref", sa.String(64)),
        sa.Column("po_line_no", sa.Integer),
    )
    op.create_index("ix_invoice_lines_invoice_id", "invoice_lines", ["invoice_id"])
    op.create_table(
        "extraction_runs",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "invoice_id",
            sa.String(32),
            sa.ForeignKey("invoices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("tokens_in", sa.Integer, nullable=False),
        sa.Column("tokens_out", sa.Integer, nullable=False),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=False),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        sa.Column("attempt", sa.Integer, nullable=False),
        sa.Column("valid", sa.Boolean, nullable=False),
        sa.Column("raw_output", postgresql.JSONB),
        sa.Column("error", sa.Text),
        _ts(),
    )
    op.create_index("ix_extraction_runs_invoice_id", "extraction_runs", ["invoice_id"])
    op.create_table(
        "validation_issues",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "invoice_id",
            sa.String(32),
            sa.ForeignKey("invoices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("field", sa.String(80), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("line", sa.Integer),
    )
    op.create_index("ix_validation_issues_invoice_id", "validation_issues", ["invoice_id"])
    op.create_table(
        "review_decisions",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "invoice_id",
            sa.String(32),
            sa.ForeignKey("invoices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.String(32), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("corrections", postgresql.JSONB),
        _ts(),
    )
    op.create_index("ix_review_decisions_invoice_id", "review_decisions", ["invoice_id"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("entity", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.String(64), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("actor", sa.String(32), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        _ts(),
    )
    op.create_index("ix_audit_events_entity_id", "audit_events", ["entity_id"])
    # Insert-only: any UPDATE or DELETE raises, whichever role runs it (owners and
    # superusers included). TRUNCATE is an admin operation and is not blocked.
    op.execute("""
        CREATE FUNCTION audit_events_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'audit_events is insert-only (% blocked)', TG_OP
            USING ERRCODE = 'insufficient_privilege';
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER audit_events_insert_only BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION audit_events_immutable();
    """)
    op.execute("REVOKE UPDATE, DELETE ON audit_events FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_insert_only ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_immutable()")
    for t in (
        "audit_events",
        "review_decisions",
        "validation_issues",
        "extraction_runs",
        "invoice_lines",
        "invoice_files",
        "invoices",
        "goods_receipts",
        "po_lines",
        "purchase_orders",
        "vendors",
    ):
        op.drop_table(t)
