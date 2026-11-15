"""Normalizers for the values invoices print: amounts, dates, identifiers.

Money is always ``Decimal`` quantized with ROUND_HALF_UP. Floats never touch money.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d.%m.%Y",
    "%d-%b-%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%Y/%m/%d",
)
_CURRENCY_MARKS = re.compile(r"(?i)(usd|eur|gbp|pkr|rs\.?|[$€£])")


def money(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_amount(raw: object) -> Decimal:
    """Parse '1,240.50', '$ 1 240.50', 'USD 1240.5', '(12.00)' or a number into Decimal.

    Only the dot is treated as the decimal separator (all generated layouts use it). A
    comma followed by exactly two digits at the end is rejected rather than guessed.
    """
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, float):
        return Decimal(repr(raw))  # repr keeps the shortest round-trip form
    if not isinstance(raw, str):
        raise ValueError(f"not an amount: {raw!r}")
    s = _CURRENCY_MARKS.sub("", raw).strip()
    negative = s.startswith("(") and s.endswith(")")
    s = re.sub(r"\s", "", s.strip("()"))  # spaces (incl. no-break) as thousands separators
    if re.search(r",\d{2}$", s):
        raise ValueError(f"ambiguous decimal comma in {raw!r}")
    s = s.replace(",", "")
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        raise ValueError(f"not an amount: {raw!r}")
    try:
        value = Decimal(s)
    except InvalidOperation as e:
        raise ValueError(f"not an amount: {raw!r}") from e
    return -value if negative else value


def parse_date(raw: object) -> date:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if not isinstance(raw, str):
        raise ValueError(f"not a date: {raw!r}")
    s = " ".join(raw.replace(",", ", ").split()).replace(" ,", ",")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date: {raw!r}")


def norm_text(s: str) -> str:
    """Case- and whitespace-insensitive comparison form."""
    return " ".join(s.split()).casefold()


def norm_id(s: str) -> str:
    """Identifiers (invoice numbers, PO numbers, tax ids): drop spaces, dashes, case."""
    return re.sub(r"[\s\-_/.]", "", s).upper()


def show(x: Decimal | None, min_places: int = 0) -> str:
    """Human display: drop trailing zeros but keep ``min_places`` decimals (115.6300 -> 115.63,
    2.000 -> 2, 12.5 with min 2 -> 12.50). Never used for arithmetic."""
    if x is None:
        return "—"
    s = format(x.normalize(), "f")
    whole, _, frac = s.partition(".")
    frac = frac.ljust(min_places, "0")
    return f"{whole}.{frac}" if frac else whole
