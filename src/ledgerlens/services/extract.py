"""LLM structured extraction with evidence verification and exactly one repair attempt.

The caller persists every element of ``runs`` (tokens, cost, latency, validity), including
failed attempts, so cost and invalid-output rates are measured, not guessed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.adapters.llm import LLMClient, LLMResult, complete_with_retries
from ledgerlens.domain.schemas import InvoiceExtraction

PROMPTS_DIR = Path("prompts")


@dataclass(frozen=True)
class Attempt:
    result: LLMResult
    valid: bool
    error: str | None


@dataclass(frozen=True)
class ExtractionOutcome:
    extraction: InvoiceExtraction
    attempts: list[Attempt]
    unsupported_fields: list[str]  # evidence failed -> confidence forced to 0


class ExtractionInvalid(Exception):
    def __init__(self, attempts: list[Attempt]) -> None:
        super().__init__(f"extraction invalid after {len(attempts)} attempt(s)")
        self.attempts = attempts


@lru_cache(maxsize=8)
def load_prompt(version: str, prompts_dir: Path = PROMPTS_DIR) -> str:
    if not re.fullmatch(r"[a-z0-9_]+", version):
        raise ValueError(f"bad prompt version {version!r}")
    return (prompts_dir / f"{version}.md").read_text(encoding="utf-8")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


def verify_evidence(ex: InvoiceExtraction, pages: list[str]) -> list[str]:
    """Each value must come with a quote that occurs on the cited page (whitespace- and
    case-insensitive). Otherwise its confidence is set to 0, which routes it to review.
    Returns the names of unsupported fields."""
    norm_pages = [_norm(p) for p in pages]
    unsupported = []
    for name, f in ex.iter_fields():
        if f.value is None:
            continue
        ok = (
            f.evidence is not None
            and 1 <= f.evidence.page <= len(pages)
            and bool(_norm(f.evidence.quote))
            and _norm(f.evidence.quote) in norm_pages[f.evidence.page - 1]
        )
        if not ok:
            f.confidence = 0.0
            unsupported.append(name)
    return unsupported


def build_messages(prompt: str, pages: list[str]) -> list[dict[str, Any]]:
    body = "\n<page_break/>\n".join(p.replace("</invoice_text>", "") for p in pages)
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"<invoice_text>\n{body}\n</invoice_text>"},
    ]


async def extract(
    llm: LLMClient,
    pages: list[str],
    *,
    prompt_version: str,
    max_attempts: int = 3,
    prompts_dir: Path = PROMPTS_DIR,
) -> ExtractionOutcome:
    schema = InvoiceExtraction.model_json_schema()
    messages = build_messages(load_prompt(prompt_version, prompts_dir), pages)
    attempts: list[Attempt] = []
    for _ in (1, 2):  # original + one repair
        res = await complete_with_retries(
            llm, max_attempts, prompt_version=prompt_version, messages=messages, schema=schema
        )
        try:
            raw = res.parsed if res.parsed is not None else json.loads(res.text or "")
            ex = InvoiceExtraction.model_validate(raw)
        except (ValidationError, json.JSONDecodeError, TypeError) as e:
            err = str(e)[:1500]
            attempts.append(Attempt(res, False, err))
            messages = [
                *messages,
                {"role": "assistant", "content": res.text or json.dumps(res.parsed)},
                {
                    "role": "user",
                    "content": f"Output failed validation: {err}. Return corrected JSON only.",
                },
            ]
            continue
        attempts.append(Attempt(res, True, None))
        unsupported = verify_evidence(ex, pages)
        return ExtractionOutcome(ex, attempts, unsupported)
    raise ExtractionInvalid(attempts)
