"""Reviewer UI: sign-in, queue, upload, detail, decisions and limits, through real HTML."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.adapters.db.api_keys import SqlApiKeyRepo
from ledgerlens.adapters.db import repo
from ledgerlens.evals.dataset import load_reference
from ledgerlens.main import create_app
from tests.conftest import make_ledger_settings
from tests.integration.test_ledgerlens import BIG, CLEAN, DEV, PRICE, drain

SM = async_sessionmaker[AsyncSession]


@pytest.fixture
async def ui(sm: SM, engine: AsyncEngine) -> AsyncIterator[tuple[httpx.AsyncClient, Any, SM]]:
    async with sm() as s:
        await repo.seed_reference(s, load_reference())
    settings = make_ledger_settings(database_url=engine.url.render_as_string(hide_password=False))
    app = create_app(settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c, settings, sm
    await app.state.engine.dispose()


async def sign_in(c: httpx.AsyncClient, sm: SM, role: str) -> None:
    async with sm() as s:
        _, raw = await SqlApiKeyRepo(s).create(f"ui-{role}", role)  # type: ignore[arg-type]
    r = await c.post("/ui/login", data={"api_key": raw})
    assert r.status_code == 303 and "ll_key" in r.cookies
    c.cookies.set("ll_key", raw)


async def test_requires_sign_in_and_rejects_bad_or_low_keys(ui: Any) -> None:
    c, _, sm = ui
    r = await c.get("/ui")
    assert r.status_code == 303 and r.headers["location"] == "/ui/login"
    assert (await c.post("/ui/login", data={"api_key": "sk_wrong"})).status_code == 401
    async with sm() as s:
        _, viewer = await SqlApiKeyRepo(s).create("v", "viewer")
    assert (await c.post("/ui/login", data={"api_key": viewer})).status_code == 401
    assert (await c.get("/")).headers["location"] == "/ui"


async def test_cookie_is_httponly_and_samesite_strict(ui: Any) -> None:
    c, _, sm = ui
    async with sm() as s:
        _, raw = await SqlApiKeyRepo(s).create("x", "clerk")
    r = await c.post("/ui/login", data={"api_key": raw})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


async def test_upload_route_and_review_flow(ui: Any) -> None:
    c, settings, sm = ui
    await sign_in(c, sm, "clerk")
    r = await c.post(
        "/ui/upload", files={"file": ("a.pdf", DEV.pdf_bytes(PRICE), "application/pdf")}
    )
    assert r.status_code == 303
    detail_url = r.headers["location"]
    page = (await c.get(detail_url)).text
    assert "hx-get" in page and "processing" in page  # polls until routed

    await drain(sm, settings)
    status = await c.get(detail_url + "/status")
    assert status.headers.get("HX-Refresh") == "true"
    page = (await c.get(detail_url)).text
    assert "PRICE_ABOVE_TOLERANCE" in page and "vs PO" in page and "needs review" in page

    queue = (await c.get("/ui?status=needs_review")).text
    assert PRICE.invoice_number in queue

    r = await c.post(
        detail_url + "/decision", data={"decision": "reject", "reason": "price not agreed"}
    )
    assert "invoice is now rejected" in r.text
    assert "price not agreed" in r.text  # audit history shows the reason


async def test_clerk_limit_shown_and_enforced_in_ui(ui: Any) -> None:
    c, settings, sm = ui
    await sign_in(c, sm, "clerk")
    r = await c.post(
        "/ui/upload", files={"file": ("big.pdf", DEV.pdf_bytes(BIG), "application/pdf")}
    )
    url = r.headers["location"]
    await drain(sm, settings)
    page = (await c.get(url)).text
    assert "up to 5000.00" in page
    r = await c.post(url + "/decision", data={"decision": "approve", "reason": "looks fine"})
    assert "Decision limit exceeded" in r.text
    assert '<span class="pill s-needs_review">' in r.text  # still waiting for an approver


async def test_bad_upload_shows_error(ui: Any) -> None:
    c, _, sm = ui
    await sign_in(c, sm, "clerk")
    r = await c.post("/ui/upload", files={"file": ("x.pdf", b"MZ not a pdf", "application/pdf")})
    assert r.status_code == 415 and "Not a PDF" in r.text


async def test_auto_approved_explains_itself_and_html_is_escaped(ui: Any) -> None:
    c, settings, sm = ui
    await sign_in(c, sm, "clerk")
    url = (
        await c.post(
            "/ui/upload", files={"file": ("c.pdf", DEV.pdf_bytes(CLEAN), "application/pdf")}
        )
    ).headers["location"]
    await drain(sm, settings)
    page = (await c.get(url)).text
    assert "Every check passed" in page
    r = await c.post(
        url + "/decision", data={"decision": "reopen", "reason": "<script>alert(1)</script>"}
    )
    assert "Insufficient role" in r.text  # only approvers reopen auto-approved invoices
    assert "<script>alert(1)</script>" not in r.text  # user input is escaped, never rendered
