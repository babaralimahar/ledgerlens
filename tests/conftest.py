from __future__ import annotations

import os
from typing import Any

import pytest
from tenacity import wait_none

import app.adapters.llm as llm_module
from app.config import Settings
from ledgerlens.config import LedgerSettings

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@127.0.0.1:5434/app_test"
)


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "env": "test",
        "database_url": TEST_DATABASE_URL,
        "llm_provider": "fake",
        "worker_mode": "off",
        "otel_endpoint": None,
        "_env_file": None,  # tests never read the developer's .env
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep retry tests fast: tenacity waits are zero in tests."""
    monkeypatch.setattr(llm_module, "_WAIT", wait_none())


def make_ledger_settings(**overrides: Any) -> LedgerSettings:
    """LedgerLens settings for tests: dataset business date, fake LLM, no embedded worker."""
    from datetime import date

    values: dict[str, Any] = {
        "env": "test",
        "database_url": TEST_DATABASE_URL,
        "llm_provider": "fake",
        "worker_mode": "off",
        "business_date": date(2026, 11, 2),
        "_env_file": None,
    }
    values.update(overrides)
    return LedgerSettings(**values)
