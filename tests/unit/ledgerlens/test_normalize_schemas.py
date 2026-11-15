from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from ledgerlens.domain.normalize import money, norm_id, parse_amount, parse_date
from ledgerlens.domain.schemas import InvoiceExtraction
from tests.unit.ledgerlens.factories import extraction, f


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,240.50", "1240.50"),
        ("$ 1,240.50", "1240.50"),
        ("USD 1 240.50", "1240.50"),
        ("1240.5", "1240.5"),
        ("(12.00)", "-12.00"),
        ("EUR 99.99", "99.99"),
        (12, "12"),
        (Decimal("3.10"), "3.10"),
        ("1\u00a0000.00", "1000.00"),
    ],
)
def test_parse_amount(raw: object, expected: str) -> None:
    assert parse_amount(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["12,50", "abc", "1.2.3", "", None, "--5"])
def test_parse_amount_rejects_ambiguous_or_garbage(raw: object) -> None:
    with pytest.raises(ValueError):
        parse_amount(raw)


def test_float_amounts_use_shortest_repr_not_binary_noise() -> None:
    assert parse_amount(0.1) == Decimal("0.1")


@pytest.mark.parametrize(
    "raw",
    [
        "2026-11-03",
        "03/11/2026",
        "03.11.2026",
        "03-Nov-2026",
        "03 Nov 2026",
        "03 November 2026",
        "Nov 03, 2026",
        "November 3, 2026",
        "2026/11/03",
    ],
)
def test_parse_date_formats(raw: str) -> None:
    assert parse_date(raw) == date(2026, 11, 3)


def test_money_rounds_half_up() -> None:
    assert money(Decimal("2.675")) == Decimal("2.68")  # float rounding would give 2.67
    assert money(Decimal("-2.675")) == Decimal("-2.68")


def test_norm_id() -> None:
    assert norm_id("po-2026 / 1001") == norm_id("PO20261001")


def test_extraction_accepts_printed_formats() -> None:
    ex = extraction(subtotal=f("$ 500.00"), invoice_date=f("20 Oct 2026"))
    assert ex.subtotal.value == Decimal("500.00")
    assert ex.invoice_date.value == date(2026, 10, 20)


def test_extraction_rejects_unparseable_amount_for_repair() -> None:
    with pytest.raises(ValidationError):
        extraction(total=f("five hundred"))


def test_extraction_forbids_extra_keys() -> None:
    with pytest.raises(ValidationError):
        extraction(approved=True)


def test_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        extraction(total={"value": "1", "evidence": None, "confidence": 1.5})


def test_json_schema_has_short_definition_names() -> None:
    schema = InvoiceExtraction.model_json_schema()
    assert set(schema["$defs"]) >= {
        "ExtractedText",
        "ExtractedAmount",
        "ExtractedDate",
        "LineItem",
        "Evidence",
    }
    json.dumps(schema)


def test_iter_fields_covers_header_and_lines() -> None:
    names = [n for n, _ in extraction().iter_fields()]
    assert "total" in names and "lines[2].unit_price" in names and len(names) == 10 + 2 * 5
    required = [n for n, _ in extraction().required_fields()]
    assert "due_date" not in required and "lines[1].po_line_ref" not in required
