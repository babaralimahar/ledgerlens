"""Day/month order: the classic AP extraction error.

"04/12/2026" is 4 December in most of the world and 12 April in the US. A model can quote the
right text and still read it the wrong way, which an evidence check alone cannot catch. These
pure functions work out what a quoted date can mean, so code (not the model) decides whether
the extracted value is supported, and sends genuinely ambiguous dates to a human.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from typing import Literal

from ledgerlens.domain.normalize import parse_date

Order = Literal["dmy", "mdy"]
_NUMERIC = re.compile(r"^\s*(\d{1,2})([/.\-])(\d{1,2})\2(\d{4})\s*$")


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def interpretations(quote: str) -> dict[Order | Literal["text"], date]:
    """Every reading of a printed date. Numeric A/B/YYYY gives up to two (dmy, mdy).
    Dots are day-first by convention (no locale writes month.day.year)."""
    m = _NUMERIC.match(quote)
    if m:
        a, sep, b, y = int(m.group(1)), m.group(2), int(m.group(3)), int(m.group(4))
        out: dict[Order | Literal["text"], date] = {}
        if (dmy := _mk(y, b, a)) is not None:
            out["dmy"] = dmy
        if sep != "." and (mdy := _mk(y, a, b)) is not None:
            out["mdy"] = mdy
        return out
    try:
        return {"text": parse_date(quote)}
    except ValueError:
        return {}


def document_order(quotes: Iterable[str]) -> Order | None:
    """The order implied by any unambiguous numeric date on the same document."""
    for q in quotes:
        cands = interpretations(q)
        if "dmy" in cands and "mdy" not in cands:
            return "dmy"
        if "mdy" in cands and "dmy" not in cands:
            return "mdy"
    return None


def resolve(quote: str, order: Order | None) -> tuple[date | None, bool]:
    """(date the quote means, ambiguous?). Ambiguous = two different readings and no order."""
    cands = interpretations(quote)
    if not cands:
        return None, False
    if "text" in cands:
        return cands["text"], False
    readings = set(cands.values())
    if len(readings) == 1:
        return readings.pop(), False
    if order is not None and order in cands:
        return cands[order], False
    return None, True
