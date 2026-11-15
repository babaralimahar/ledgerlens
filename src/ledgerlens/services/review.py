"""Human decisions. Authorization lives here, in the service layer, not only in routers:
any caller (API, CLI, a future MCP tool) gets the same role and amount checks."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import Principal
from app.api.errors import DomainError
from app.domain.roles import has_role
from ledgerlens.adapters.db import repo
from ledgerlens.domain.normalize import money, parse_amount, parse_date
from ledgerlens.domain.policy import Policy
from ledgerlens.domain.states import InvalidTransition

DecisionKind = Literal["approve", "reject", "correct", "reopen"]
CORRECTABLE = frozenset(
    {"invoice_number", "invoice_date", "due_date", "po_number", "subtotal", "tax", "total"}
)
_TARGET = {
    "approve": "approved",
    "reject": "rejected",
    "correct": "needs_review",
    "reopen": "needs_review",
}


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return f"{v:.2f}"
    if isinstance(v, date):
        return v.isoformat()
    return v


def parse_corrections(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Typed corrections: money as Decimal, dates as date, ids as trimmed strings."""
    if not raw or not set(raw) <= CORRECTABLE:
        raise DomainError(
            422, "bad-corrections", "Invalid corrections", f"allowed fields: {sorted(CORRECTABLE)}"
        )
    out: dict[str, Any] = {}
    try:
        for k, v in raw.items():
            if k in ("subtotal", "tax", "total"):
                out[k] = money(parse_amount(v))
            elif k in ("invoice_date", "due_date"):
                out[k] = parse_date(v)
            else:
                out[k] = str(v).strip()[:64] or None
    except ValueError as e:
        raise DomainError(422, "bad-corrections", "Invalid corrections", str(e)) from e
    return out


def check_authority(
    p: Principal, decision: DecisionKind, total: Any, currency: str | None, policy: Policy
) -> None:
    if not has_role(p.role, "clerk"):
        raise DomainError(403, "forbidden", "Insufficient role", "requires clerk")
    if decision == "reopen" and not has_role(p.role, "approver"):
        raise DomainError(
            403, "forbidden", "Insufficient role", "only approvers reopen auto-approved invoices"
        )
    if decision in ("approve", "reject") and not has_role(p.role, "approver"):
        limit = policy.clerk_limit(currency)
        if total is None or total > limit:
            raise DomainError(
                403,
                "decision-limit",
                "Decision limit exceeded",
                f"Role '{p.role}' may decide invoices up to {limit:.2f} {currency or ''}".rstrip(),
            )


async def decide_invoice(
    session: AsyncSession,
    p: Principal,
    invoice_id: str,
    decision: DecisionKind,
    reason: str,
    corrections: dict[str, Any] | None,
    policy: Policy,
) -> str:
    """Apply one decision in one transaction. Returns the new status."""
    async with repo.atomic(session):
        inv = await repo.lock_invoice(session, invoice_id)
        if inv is None:
            raise DomainError(404, "not-found", "Invoice not found")
        check_authority(p, decision, inv.total, inv.currency, policy)
        if decision == "correct":
            parsed = parse_corrections(corrections)
            before = {k: _jsonable(getattr(inv, k)) for k in parsed}
            for k, v in parsed.items():
                setattr(inv, k, v)
            corrections = {"before": before, "after": {k: _jsonable(v) for k, v in parsed.items()}}
        target = _TARGET[decision]
        try:
            await repo.set_status(
                session, inv, target, p.key_id, {"decision": decision, "reason": reason}
            )
        except InvalidTransition as e:
            raise DomainError(
                409, "conflict", "Invalid state", f"cannot {decision} an invoice in '{e.old}'"
            ) from e
        await repo.add_decision(
            session, invoice_id, p.key_id, p.role, decision, reason, corrections
        )
    return target
