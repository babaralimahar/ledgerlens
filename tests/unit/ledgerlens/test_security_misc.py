from __future__ import annotations

from decimal import Decimal

import pytest

from app.api.auth import Principal
from app.api.errors import DomainError
from app.observability.logging import REDACTED, scrub
from ledgerlens.api.ratelimit import SlidingWindowLimiter
from ledgerlens.domain.policy import Policy
from ledgerlens.services.review import check_authority, parse_corrections
from tests.conftest import make_ledger_settings

CLERK, APPROVER, VIEWER = (
    Principal("k1", "clerk"),
    Principal("k2", "approver"),
    Principal("k3", "viewer"),
)


def test_rate_limiter_window() -> None:
    lim = SlidingWindowLimiter(limit=2, window_s=60)
    lim.check("a", now=0)
    lim.check("a", now=1)
    with pytest.raises(DomainError) as e:
        lim.check("a", now=2)
    assert e.value.status == 429
    lim.check("b", now=2)  # per key
    lim.check("a", now=61)  # window slid


def test_clerk_limit_enforced_in_service_layer() -> None:
    p = Policy()
    check_authority(CLERK, "approve", Decimal("5000.00"), "USD", p)
    with pytest.raises(DomainError) as e:
        check_authority(CLERK, "approve", Decimal("5000.01"), "USD", p)
    assert e.value.status == 403 and e.value.code == "decision-limit"
    assert "5000.00 USD" in e.value.detail
    check_authority(APPROVER, "approve", Decimal("1000000"), "USD", p)


def test_clerk_cannot_decide_unknown_amount_or_currency() -> None:
    with pytest.raises(DomainError):
        check_authority(CLERK, "reject", None, "USD", Policy())
    with pytest.raises(DomainError):
        check_authority(CLERK, "approve", Decimal("1"), "JPY", Policy())


def test_viewer_and_reopen_rules() -> None:
    with pytest.raises(DomainError):
        check_authority(VIEWER, "approve", Decimal("1"), "USD", Policy())
    with pytest.raises(DomainError):
        check_authority(CLERK, "reopen", Decimal("1"), "USD", Policy())
    check_authority(APPROVER, "reopen", Decimal("1"), "USD", Policy())
    check_authority(
        CLERK, "correct", Decimal("999999"), "USD", Policy()
    )  # correcting is not deciding


def test_corrections_are_typed_and_whitelisted() -> None:
    out = parse_corrections(
        {"total": "1,240.50", "invoice_date": "03 Nov 2026", "po_number": " PO-1 "}
    )
    assert (
        out["total"] == Decimal("1240.50")
        and str(out["invoice_date"]) == "2026-11-03"
        and out["po_number"] == "PO-1"
    )
    for bad in [{"status": "approved"}, {"total": "lots"}, {}]:
        with pytest.raises(DomainError):
            parse_corrections(bad)


def test_bank_details_are_scrubbed_from_logs() -> None:
    line = "remit to IBAN PK36SCBL0000001123456702 or GB82WEST12345698765432 ref INV-2026-0042"
    out = scrub(line)
    assert "PK36SCBL0000001123456702" not in out and "GB82WEST12345698765432" not in out
    assert out.count(REDACTED) == 2 and "INV-2026-0042" in out


def test_settings_policy_scales_limits() -> None:
    s = make_ledger_settings(auto_approve_limit_usd=Decimal("1000"))
    pol = s.policy()
    assert pol.auto_limit("USD") == Decimal("1000") and pol.auto_limit("PKR") == Decimal("280000")
    assert s.today().isoformat() == "2026-11-02"
