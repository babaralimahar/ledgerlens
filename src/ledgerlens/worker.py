"""Standalone worker: ``python -m ledgerlens.worker`` (set WORKER_MODE=off on the web process)."""

from __future__ import annotations

import asyncio
import contextlib
import signal

from app.adapters.db.engine import create_engine, create_sessionmaker
from app.adapters.llm import load_pricing
from app.adapters.llm_factory import build_llm_client
from app.main import AppContext
from app.observability.logging import configure_logging
from app.worker import Worker
from ledgerlens.config import get_ledger_settings
from ledgerlens.services.pipeline import build_handlers


async def main() -> None:
    settings = get_ledger_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings)
    sm = create_sessionmaker(engine)
    llm = build_llm_client(settings, load_pricing(settings.pricing_path))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    worker = Worker(
        sm,
        build_handlers(AppContext(settings, sm, llm)),
        poll_s=settings.worker_poll_s,
        lock_timeout_s=settings.job_lock_timeout_s,
    )
    try:
        await worker.run_forever(stop)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
