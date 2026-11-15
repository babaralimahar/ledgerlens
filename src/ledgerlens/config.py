"""LedgerLens settings: the template's settings plus the business policy."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from functools import lru_cache
from types import MappingProxyType

from pydantic import Field

from app.config import Settings
from ledgerlens.domain.policy import Policy


class LedgerSettings(Settings):
    app_name: str = "ledgerlens"
    max_request_bytes: int = Field(11 * 1024 * 1024, ge=1024)  # 10 MB file + multipart overhead
    max_upload_bytes: int = Field(10 * 1024 * 1024, ge=1024)
    upload_rate_per_minute: int = Field(30, ge=1)  # per API key, per process
    prompt_version: str = "extract_v1"
    pdf_parse_timeout_s: float = Field(20.0, gt=0)
    ocr_min_chars_per_page: int = Field(50, ge=0)
    job_max_attempts: int = Field(3, ge=1, le=10)
    # Business date for FUTURE_DATE checks. None = today. Set it only for demos/evals on
    # synthetic data whose dates are relative to a fixed day.
    business_date: date | None = None

    price_tolerance_pct: Decimal = Decimal("2")
    confidence_threshold: float = Field(0.8, ge=0, le=1)
    vendor_name_threshold: float = Field(90.0, ge=0, le=100)
    auto_approve_limit_usd: Decimal = Decimal("5000")
    clerk_decision_limit_usd: Decimal = Decimal("5000")

    def policy(self) -> Policy:
        base = Policy()
        scale_auto = self.auto_approve_limit_usd / base.auto_approve_limits["USD"]
        scale_clerk = self.clerk_decision_limit_usd / base.clerk_decision_limits["USD"]
        return Policy(
            price_tolerance_pct=self.price_tolerance_pct,
            confidence_threshold=self.confidence_threshold,
            vendor_name_threshold=self.vendor_name_threshold,
            auto_approve_limits=MappingProxyType(
                {
                    k: (v * scale_auto).quantize(Decimal("1"))
                    for k, v in base.auto_approve_limits.items()
                }
            ),
            clerk_decision_limits=MappingProxyType(
                {
                    k: (v * scale_clerk).quantize(Decimal("1"))
                    for k, v in base.clerk_decision_limits.items()
                }
            ),
        )

    def today(self) -> date:
        return self.business_date or date.today()


@lru_cache
def get_ledger_settings() -> LedgerSettings:
    return LedgerSettings()
