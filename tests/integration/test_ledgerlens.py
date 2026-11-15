"""End to end on real Postgres: upload -> queue -> worker (oracle model) -> routing -> review."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.adapters.db import jobs
from app.adapters.db.api_keys import SqlApiKeyRepo
from app.adapters.llm import FakeLLMClient, LLMTimeout
from app.main import AppContext
from app.worker import Worker
from ledgerlens.adapters.db import repo
from ledgerlens.adapters.db.models import AuditEventRow, InvoiceRow
from ledgerlens.domain.ground_truth import InvoiceGroundTruth
from ledgerlens.domain.policy import Policy
from ledgerlens.domain.states import InvalidTransition
from ledgerlens.evals.dataset import load_invoices, load_reference
from ledgerlens.evals.oracle import oracle_llm
from ledgerlens.main import create_app
from ledgerlens.services.pipeline import Pipeline, build_handlers
from tests.conftest import make_ledger_settings

SM = async_sessionmaker[AsyncSession]
DEV = load_invoices("dev")


def pick(pred: Any) -> InvoiceGroundTruth:
    return next(g for g in DEV.items if pred(g))


CLEAN = pick(
    lambda g: (
        g.expected_route == "auto_approved"
        and not g.duplicate_of
        and g.id not in {d.duplicate_of for d in DEV.items}
    )
)
PRICE = pick(  # one a clerk may decide (within the clerk limit for its currency)
    lambda g: "PRICE_ABOVE_TOLERANCE" in g.anomalies and g.total <= Policy().clerk_limit(g.currency)
)
INJECTION = pick(lambda g: g.anomalies == ["PROMPT_INJECTION"])
SCAN = pick(lambda g: "NEEDS_OCR" in g.anomalies)
DUP = pick(lambda g: g.duplicate_of is not None)
DUP_ORIGINAL = next(g for g in DEV.items if g.id == DUP.duplicate_of)
BIG = pick(
    lambda g: not g.anomalies and g.expected_route == "needs_review"
)  # clean, above auto limit


@pytest.fixture
async def seeded(sm: SM) -> SM:
    async with sm() as s:
        await repo.seed_reference(s, load_reference())
    return sm


@pytest.fixture
def settings(engine: AsyncEngine) -> Any:
    return make_ledger_settings(database_url=engine.url.render_as_string(hide_password=False))


@pytest.fixture
async def app_client(seeded: SM, settings: Any) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        yield c, app
    await app.state.engine.dispose()


async def key(sm: SM, role: str) -> dict[str, str]:
    async with sm() as s:
        _, raw = await SqlApiKeyRepo(s).create(f"t-{role}", role)  # type: ignore[arg-type]
    return {"X-API-Key": raw}


async def upload(
    c: httpx.AsyncClient, headers: dict[str, str], data: bytes, name: str = "inv.pdf"
) -> httpx.Response:
    return await c.post(
        "/v1/invoices", headers=headers, files={"file": (name, data, "application/pdf")}
    )


def pipeline(sm: SM, settings: Any, llm: Any = None) -> Pipeline:
    return Pipeline(sm, llm or oracle_llm(DEV.items), settings)


async def drain(sm: SM, settings: Any, llm: Any = None) -> int:
    handlers = build_handlers(AppContext(settings, sm, llm or oracle_llm(DEV.items)))
    w = Worker(sm, handlers, worker_id="w-test")
    n = 0
    while await w.run_once():
        n += 1
    return n


# ------------------------------------------------------------------ upload


async def test_same_file_twice_returns_same_invoice_and_one_row(
    app_client: Any, seeded: SM
) -> None:
    c, _ = app_client
    h = await key(seeded, "clerk")
    pdf = DEV.pdf_bytes(CLEAN)
    r1, r2 = await upload(c, h, pdf), await upload(c, h, pdf, "renamed.pdf")
    assert r1.status_code == r2.status_code == 202
    assert r1.json()["invoice_id"] == r2.json()["invoice_id"]
    assert r1.json()["job_id"] == r2.json()["job_id"] and r1.json()["job_id"]
    async with seeded() as s:
        assert await s.scalar(text("SELECT count(*) FROM invoices")) == 1
        assert await s.scalar(text("SELECT count(*) FROM jobs")) == 1


@pytest.mark.parametrize(
    ("data", "status"),
    [(b"MZ\x90 not a pdf", 415), (b"", 422)],
)
async def test_bad_uploads_rejected_with_problem_json(
    app_client: Any, seeded: SM, data: bytes, status: int
) -> None:
    c, _ = app_client
    r = await upload(c, await key(seeded, "clerk"), data)
    assert r.status_code == status and r.headers["content-type"].startswith(
        "application/problem+json"
    )


async def test_oversized_upload_rejected(seeded: SM, settings: Any) -> None:
    app = create_app(
        settings.model_copy(update={"max_upload_bytes": 2048, "max_request_bytes": 1_000_000})
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await upload(c, await key(seeded, "clerk"), b"%PDF-1.4\n" + b"0" * 5000)
    assert r.status_code == 413 and r.json()["type"].endswith("payload-too-large")
    await app.state.engine.dispose()


async def test_viewer_cannot_upload(app_client: Any, seeded: SM) -> None:
    c, _ = app_client
    assert (await upload(c, await key(seeded, "viewer"), DEV.pdf_bytes(CLEAN))).status_code == 403


async def test_upload_rate_limit(seeded: SM, settings: Any) -> None:
    app = create_app(settings.model_copy(update={"upload_rate_per_minute": 2}))
    h = await key(seeded, "clerk")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        codes = [(await upload(c, h, DEV.pdf_bytes(g))).status_code for g in DEV.items[:3]]
    assert codes == [202, 202, 429]
    await app.state.engine.dispose()


# ------------------------------------------------------------------ processing


async def test_demo_scenario_clean_price_variance_injection(
    app_client: Any, seeded: SM, settings: Any
) -> None:
    c, _ = app_client
    h = await key(seeded, "clerk")
    ids = {}
    for name, gt in [("clean", CLEAN), ("price", PRICE), ("injection", INJECTION), ("scan", SCAN)]:
        ids[name] = (await upload(c, h, DEV.pdf_bytes(gt))).json()["invoice_id"]
    assert await drain(seeded, settings) == 4

    clean = (await c.get(f"/v1/invoices/{ids['clean']}", headers=h)).json()
    assert clean["status"] == "auto_approved" and clean["reasons"] == []
    assert clean["totals"]["total"] == f"{CLEAN.total:.2f}" and len(clean["lines"]) == len(
        CLEAN.lines
    )
    assert clean["extraction"]["model"] == "oracle" and clean["extraction"]["attempts"] == 1
    assert [e["action"] for e in clean["history"]] == [
        "uploaded",
        "status:processing",
        "status:auto_approved",
    ]

    price = (await c.get(f"/v1/invoices/{ids['price']}", headers=h)).json()
    assert price["status"] == "needs_review"
    reason = next(r for r in price["reasons"] if r["code"] == "PRICE_ABOVE_TOLERANCE")
    assert "vs PO" in reason["message"] and reason["line"] is not None

    inj = (await c.get(f"/v1/invoices/{ids['injection']}", headers=h)).json()
    assert inj["status"] == "needs_review"
    assert "SUSPICIOUS_INSTRUCTIONS" in {r["code"] for r in inj["reasons"]}

    scan = (await c.get(f"/v1/invoices/{ids['scan']}", headers=h)).json()
    assert scan["status"] == "needs_review" and scan["reasons"][0]["code"] == "NEEDS_OCR"
    assert scan["extraction"] is None  # the model is never called on an empty text layer

    queue = (await c.get("/v1/review-queue", headers=h)).json()
    assert {i["invoice_id"] for i in queue["items"]} == {
        ids["price"],
        ids["injection"],
        ids["scan"],
    }
    assert all(i["reasons"] for i in queue["items"])


async def test_duplicate_invoice_in_a_different_file_is_flagged(
    app_client: Any, seeded: SM, settings: Any
) -> None:
    c, _ = app_client
    h = await key(seeded, "clerk")
    first = (await upload(c, h, DEV.pdf_bytes(DUP_ORIGINAL))).json()["invoice_id"]
    await drain(seeded, settings)
    second = (await upload(c, h, DEV.pdf_bytes(DUP))).json()["invoice_id"]
    assert first != second
    await drain(seeded, settings)
    view = (await c.get(f"/v1/invoices/{second}", headers=h)).json()
    assert view["status"] == "needs_review" and view["duplicate_of"] == first
    assert "DUPLICATE_INVOICE" in {r["code"] for r in view["reasons"]}


async def test_reprocessing_is_idempotent(seeded: SM, settings: Any) -> None:
    async with seeded() as s:
        inv_id, _ = await repo.insert_upload(
            s, file_sha256="a" * 64, content=DEV.pdf_bytes(PRICE), filename=None, actor="t"
        )
        await s.commit()
    p = pipeline(seeded, settings)
    assert await p.process(inv_id) == "needs_review"
    assert await p.process(inv_id) == "needs_review"  # second delivery: skipped
    async with seeded() as s:
        n_lines = await s.scalar(
            text("SELECT count(*) FROM invoice_lines WHERE invoice_id = :i"), {"i": inv_id}
        )
        n_runs = await s.scalar(
            text("SELECT count(*) FROM extraction_runs WHERE invoice_id = :i"), {"i": inv_id}
        )
    assert n_lines == len(PRICE.lines) and n_runs == 1


async def test_worker_crash_mid_job_recovers_once(
    app_client: Any, seeded: SM, settings: Any
) -> None:
    c, _ = app_client
    h = await key(seeded, "clerk")
    inv_id = (await upload(c, h, DEV.pdf_bytes(CLEAN))).json()["invoice_id"]
    async with seeded() as s:  # a worker claims the job, flips the invoice to processing, then dies
        job = await jobs.claim(s, "dead-worker")
    assert job is not None
    async with seeded() as s, s.begin():
        inv = await repo.lock_invoice(s, inv_id)
        assert inv is not None
        await repo.set_status(s, inv, "processing", "dead-worker")
    async with seeded() as s:
        await s.execute(text("UPDATE jobs SET locked_at = now() - interval '1 hour'"))
        await s.commit()
    async with seeded() as s:
        assert await jobs.reap(s, timeout_s=settings.job_lock_timeout_s) == 1
    assert await drain(seeded, settings) == 1
    view = (await c.get(f"/v1/invoices/{inv_id}", headers=h)).json()
    assert view["status"] == "auto_approved"
    assert sum(1 for e in view["history"] if e["action"] == "status:auto_approved") == 1


async def test_provider_outage_dead_letters_then_requeue_works(
    app_client: Any, seeded: SM, settings: Any
) -> None:
    c, _ = app_client
    h, admin = await key(seeded, "clerk"), await key(seeded, "admin")
    inv_id = (await upload(c, h, DEV.pdf_bytes(CLEAN))).json()["invoice_id"]
    down = FakeLLMClient([LLMTimeout(str(i)) for i in range(50)])
    for _ in range(settings.job_max_attempts):
        assert await drain(seeded, settings, down) == 1
        async with seeded() as s:  # skip the backoff wait
            await s.execute(text("UPDATE jobs SET run_after = now()"))
            await s.commit()
    view = (await c.get(f"/v1/invoices/{inv_id}", headers=h)).json()
    assert view["status"] == "failed"
    job = (await c.get(f"/v1/jobs/{view['job_id']}", headers=h)).json()
    assert job["status"] == "dead" and "LLMTimeout" in job["last_error"] and job["attempts"] == 3

    assert (await c.post(f"/v1/admin/jobs/{view['job_id']}/requeue", headers=h)).status_code == 403
    r = await c.post(f"/v1/admin/jobs/{view['job_id']}/requeue", headers=admin)
    assert r.status_code == 200 and r.json()["requeued"] is True
    assert await drain(seeded, settings) == 1  # provider back
    assert (await c.get(f"/v1/invoices/{inv_id}", headers=h)).json()["status"] == "auto_approved"


async def test_invalid_transition_raises(seeded: SM) -> None:
    async with seeded() as s:
        inv_id, _ = await repo.insert_upload(
            s, file_sha256="b" * 64, content=b"%PDF-", filename=None, actor="t"
        )
        await s.commit()
    async with seeded() as s, s.begin():
        inv = await repo.lock_invoice(s, inv_id)
        assert inv is not None
        with pytest.raises(InvalidTransition):
            await repo.set_status(s, inv, "approved", "t")


# ------------------------------------------------------------------ review + audit


async def _routed(c: httpx.AsyncClient, sm: SM, settings: Any, gt: InvoiceGroundTruth) -> str:
    inv_id: str = (await upload(c, await key(sm, "clerk"), DEV.pdf_bytes(gt))).json()["invoice_id"]
    await drain(sm, settings)
    return inv_id


async def test_clerk_above_limit_403_approver_200(
    app_client: Any, seeded: SM, settings: Any
) -> None:
    c, _ = app_client
    inv_id = await _routed(c, seeded, settings, BIG)
    clerk, approver = await key(seeded, "clerk"), await key(seeded, "approver")
    body = {"decision": "approve", "reason": "checked against contract"}
    r = await c.post(f"/v1/invoices/{inv_id}/decision", headers=clerk, json=body)
    assert r.status_code == 403
    assert (
        r.json()["type"].endswith("decision-limit")
        and "may decide invoices up to" in r.json()["detail"]
    )
    r = await c.post(f"/v1/invoices/{inv_id}/decision", headers=approver, json=body)
    assert r.status_code == 200 and r.json()["status"] == "approved"
    view = (await c.get(f"/v1/invoices/{inv_id}", headers=clerk)).json()
    assert view["decisions"][0]["role"] == "approver"
    assert view["history"][-1]["action"] == "status:approved"
    r = await c.post(f"/v1/invoices/{inv_id}/decision", headers=approver, json=body)
    assert r.status_code == 409  # already decided


async def test_clerk_can_decide_within_limit_and_correct(
    app_client: Any, seeded: SM, settings: Any
) -> None:
    c, _ = app_client
    inv_id = await _routed(c, seeded, settings, PRICE)
    clerk = await key(seeded, "clerk")
    r = await c.post(
        f"/v1/invoices/{inv_id}/decision",
        headers=clerk,
        json={
            "decision": "correct",
            "reason": "vendor confirmed PO",
            "corrections": {"po_number": "PO-X"},
        },
    )
    assert r.status_code == 200 and r.json()["status"] == "needs_review"
    r = await c.post(
        f"/v1/invoices/{inv_id}/decision",
        headers=clerk,
        json={"decision": "reject", "reason": "price not agreed"},
    )
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    view = (await c.get(f"/v1/invoices/{inv_id}", headers=clerk)).json()
    assert view["po_number"] == "PO-X"
    assert view["decisions"][0]["corrections"]["before"]["po_number"] == PRICE.po_number


async def test_audit_events_are_insert_only(seeded: SM) -> None:
    async with seeded() as s:
        await repo.audit(s, "invoice", "inv_x", "test", "t")
        await s.commit()
    for stmt in ("UPDATE audit_events SET action = 'tampered'", "DELETE FROM audit_events"):
        async with seeded() as s:
            with pytest.raises(DBAPIError, match="insert-only"):
                await s.execute(text(stmt))
    async with seeded() as s:
        assert (await s.scalars(select(AuditEventRow.action))).all() == ["test"]


async def test_status_changes_always_leave_an_audit_trail(
    app_client: Any, seeded: SM, settings: Any
) -> None:
    c, _ = app_client
    await _routed(c, seeded, settings, CLEAN)
    async with seeded() as s:
        statuses = (await s.scalars(select(InvoiceRow.status))).all()
        actions = (await s.scalars(select(AuditEventRow.action).order_by(AuditEventRow.id))).all()
    assert statuses == ["auto_approved"]
    assert actions == ["uploaded", "status:processing", "status:auto_approved"]
