"""Reviewer pages. They call the same services as the JSON API, so role and amount limits are
enforced identically. Sign-in stores the API key in an HttpOnly, SameSite=Strict cookie."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.adapters.db.api_keys import SqlApiKeyRepo, hash_key
from app.api.auth import Principal
from app.api.deps import SessionDep
from app.api.errors import DomainError
from app.domain.roles import has_role
from ledgerlens.adapters.db import repo
from ledgerlens.api.routes import SettingsDep, limiter
from ledgerlens.services import intake, review

COOKIE = "ll_key"
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
router = APIRouter(prefix="/ui", include_in_schema=False)

STATUS_TABS = ("needs_review", "auto_approved", "approved", "rejected", "failed")


async def _principal(request: Request, session: Any) -> Principal | None:
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    rec = await SqlApiKeyRepo(session).find_by_hash(hash_key(raw))
    if rec is None or rec.disabled or not has_role(rec.role, "clerk"):
        return None
    return Principal(rec.id, rec.role)


def _to_login() -> RedirectResponse:
    return RedirectResponse("/ui/login", status_code=303)


def _page(request: Request, name: str, p: Principal | None, **ctx: Any) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {"me": p, **ctx})


@router.get("/login")
async def login_form(request: Request) -> HTMLResponse:
    return _page(request, "login.html", None, error=None)


@router.post("/login")
async def login(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    api_key: Annotated[str, Form(max_length=200)],
) -> Response:
    rec = await SqlApiKeyRepo(session).find_by_hash(hash_key(api_key.strip()))
    if rec is None or rec.disabled or not has_role(rec.role, "clerk"):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"me": None, "error": "Key not recognised, or role below clerk."},
            status_code=401,
        )
    resp = RedirectResponse("/ui", status_code=303)
    resp.set_cookie(
        COOKIE,
        api_key.strip(),
        httponly=True,
        samesite="strict",
        secure=settings.env == "prod",
        max_age=8 * 3600,
    )
    return resp


@router.post("/logout")
async def logout() -> Response:
    resp = _to_login()
    resp.delete_cookie(COOKIE)
    return resp


@router.get("")
async def queue(
    request: Request, session: SessionDep, status: str = "needs_review", after: str | None = None
) -> Response:
    p = await _principal(request, session)
    if p is None:
        return _to_login()
    status = status if status in STATUS_TABS else "needs_review"
    items, nxt = await repo.review_queue(session, status, 25, after)
    return _page(
        request, "queue.html", p, items=items, next_after=nxt, status=status, tabs=STATUS_TABS
    )


@router.post("/upload")
async def upload(
    request: Request, session: SessionDep, settings: SettingsDep, file: UploadFile
) -> Response:
    p = await _principal(request, session)
    if p is None:
        return _to_login()
    try:
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
    except DomainError as e:
        items, nxt = await repo.review_queue(session, "needs_review", 25, None)
        return templates.TemplateResponse(
            request,
            "queue.html",
            {
                "me": p,
                "items": items,
                "next_after": nxt,
                "status": "needs_review",
                "tabs": STATUS_TABS,
                "error": f"{e.title}: {e.detail}",
            },
            status_code=e.status,
        )
    return RedirectResponse(f"/ui/invoices/{res.invoice_id}", status_code=303)


@router.get("/invoices/{invoice_id}")
async def detail(
    request: Request, invoice_id: str, session: SessionDep, settings: SettingsDep
) -> Response:
    p = await _principal(request, session)
    if p is None:
        return _to_login()
    view = await repo.invoice_view(session, invoice_id)
    if view is None:
        return _page(request, "missing.html", p)
    limit = settings.policy().clerk_limit(view["totals"]["currency"])
    return _page(request, "invoice.html", p, inv=view, clerk_limit=limit, message=None, error=None)


@router.get("/invoices/{invoice_id}/status")
async def status_fragment(request: Request, invoice_id: str, session: SessionDep) -> Response:
    """Polled by HTMX while the worker runs; tells the page to reload once routed."""
    p = await _principal(request, session)
    if p is None:
        return Response(status_code=401)
    view = await repo.invoice_view(session, invoice_id)
    if view is None:
        return Response(status_code=404)
    resp = _page(request, "_status.html", p, inv=view)
    if view["status"] not in ("queued", "processing"):
        resp.headers["HX-Refresh"] = "true"
    return resp


@router.post("/invoices/{invoice_id}/decision")
async def decide(
    request: Request,
    invoice_id: str,
    session: SessionDep,
    settings: SettingsDep,
    decision: Annotated[str, Form()],
    reason: Annotated[str, Form(max_length=1000)],
    field: Annotated[str | None, Form()] = None,
    value: Annotated[str | None, Form(max_length=64)] = None,
) -> Response:
    p = await _principal(request, session)
    if p is None:
        return _to_login()
    error = message = None
    corrections = {field: value} if decision == "correct" and field and value else None
    try:
        if decision not in ("approve", "reject", "correct", "reopen"):
            raise DomainError(422, "bad-decision", "Unknown decision")
        if len(reason.strip()) < 3:
            raise DomainError(422, "bad-reason", "A reason is required", "at least 3 characters")
        new = await review.decide_invoice(
            session,
            p,
            invoice_id,
            cast(review.DecisionKind, decision),  # validated just above
            reason.strip(),
            corrections,
            settings.policy(),
        )
        message = f"Saved: invoice is now {new.replace('_', ' ')}."
    except DomainError as e:
        error = f"{e.title}. {e.detail}".strip()
    view = await repo.invoice_view(session, invoice_id)
    if view is None:
        return _page(request, "missing.html", p)
    limit = settings.policy().clerk_limit(view["totals"]["currency"])
    return _page(
        request, "invoice.html", p, inv=view, clerk_limit=limit, message=message, error=error
    )
