"""PDF text extraction with pdfplumber: visible text per page, hidden-text detection and an
OCR flag for pages with (almost) no text layer."""

from __future__ import annotations

import asyncio
import io
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pdfplumber

PDF_MAGIC = b"%PDF-"


class PdfUnreadable(Exception): ...


@dataclass(frozen=True)
class PdfText:
    pages: list[str]  # visible text only
    char_counts: list[int]
    hidden_chars: int
    needs_ocr: bool

    @property
    def page_count(self) -> int:
        return len(self.pages)


def is_pdf(data: bytes) -> bool:
    """Magic-byte check. The spec allows up to 1 KiB of junk before the header."""
    return PDF_MAGIC in data[:1024]


def _is_white(color: Any) -> bool:
    if color is None:
        return False
    if isinstance(color, int | float):
        return float(color) >= 0.95
    if isinstance(color, Sequence) and not isinstance(color, str):
        vals = [float(c) for c in color if isinstance(c, int | float)]
        if len(vals) == 1:  # gray
            return vals[0] >= 0.95
        if len(vals) == 3:  # rgb
            return all(v >= 0.95 for v in vals)
        if len(vals) == 4:  # cmyk
            return all(v <= 0.05 for v in vals)
    return False


def _is_hidden(obj: dict[str, Any]) -> bool:
    """White fill or microscopic glyphs: present in the text layer, invisible to a human."""
    return _is_white(obj.get("non_stroking_color")) or float(obj.get("size", 10)) < 1.0


def extract_text(data: bytes, *, min_chars_per_page: int = 50, max_pages: int = 20) -> PdfText:
    if not is_pdf(data):
        raise PdfUnreadable("not a PDF (magic bytes missing)")
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) > max_pages:
                raise PdfUnreadable(f"{len(pdf.pages)} pages exceeds the {max_pages}-page limit")
            pages: list[str] = []
            counts: list[int] = []
            hidden = 0
            for page in pdf.pages:
                chars = page.chars
                hidden += sum(1 for c in chars if _is_hidden(c) and c.get("text", "").strip())
                visible = page.filter(lambda o: o.get("object_type") != "char" or not _is_hidden(o))
                text = visible.extract_text() or ""
                pages.append(text)
                counts.append(len("".join(text.split())))
    except PdfUnreadable:
        raise
    except Exception as e:  # pdfminer raises many types for malformed files
        raise PdfUnreadable(f"cannot parse PDF: {type(e).__name__}") from e
    needs_ocr = not pages or all(c < min_chars_per_page for c in counts)
    return PdfText(pages, counts, hidden, needs_ocr)


async def extract_text_async(
    data: bytes, *, timeout_s: float, min_chars_per_page: int = 50
) -> PdfText:
    """Parse in a worker thread with a deadline, so a hostile PDF cannot stall the worker loop.
    (The thread itself cannot be killed; the deadline bounds how long the job waits.)"""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(extract_text, data, min_chars_per_page=min_chars_per_page), timeout_s
        )
    except TimeoutError as e:
        raise PdfUnreadable(f"parsing exceeded {timeout_s}s") from e
