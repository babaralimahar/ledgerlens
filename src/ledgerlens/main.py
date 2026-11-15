"""LedgerLens app. Run with: uvicorn ledgerlens.main:create_app --factory"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.main import create_app as create_base_app
from ledgerlens import __version__
from ledgerlens.api.ratelimit import SlidingWindowLimiter
from ledgerlens.api.routes import admin, router
from ledgerlens.config import LedgerSettings, get_ledger_settings
from ledgerlens.services.pipeline import build_handlers
from ledgerlens.ui.routes import router as ui_router


def create_app(settings: LedgerSettings | None = None) -> FastAPI:
    settings = settings or get_ledger_settings()
    app = create_base_app(
        settings,
        routers=[router, admin, ui_router],
        handlers=build_handlers,
        title="LedgerLens",
        version=__version__,
    )
    app.state.upload_limiter = SlidingWindowLimiter(settings.upload_rate_per_minute)

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/ui")

    return app
