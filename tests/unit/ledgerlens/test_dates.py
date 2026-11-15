from __future__ import annotations

from datetime import date

import pytest

from ledgerlens.domain.dates import document_order, interpretations, resolve
from ledgerlens.domain.rules import check_date_evidence
from tests.unit.ledgerlens.factories import extraction, f


def test_interpretations() -> None:
    assert interpretations("04/12/2026") == {"dmy": date(2026, 12, 4), "mdy": date(2026, 4, 12)}
    assert interpretations("17/09/2026") == {"dmy": date(2026, 9, 17)}  # no 17th month
    assert interpretations("09/17/2026") == {"mdy": date(2026, 9, 17)}
    assert interpretations("04.12.2026") == {"dmy": date(2026, 12, 4)}  # dots are day-first
    assert interpretations("03 Nov 2026") == {"text": date(2026, 11, 3)}
    assert interpretations("garbage") == {}


def test_document_order() -> None:
    assert document_order(["04/12/2026", "17/01/2027"]) == "dmy"
    assert document_order(["04/12/2026", "01/17/2027"]) == "mdy"
    assert document_order(["04/12/2026", "03/01/2027"]) is None


@pytest.mark.parametrize(
    ("quote", "order", "expected"),
    [
        ("04/12/2026", "dmy", (date(2026, 12, 4), False)),
        ("04/12/2026", None, (None, True)),
        ("05/05/2026", None, (date(2026, 5, 5), False)),
        ("2026-11-03", None, (date(2026, 11, 3), False)),
    ],
)
def test_resolve(quote: str, order: str | None, expected: tuple[date | None, bool]) -> None:
    assert resolve(quote, order) == expected  # type: ignore[arg-type]


def test_wrong_reading_is_an_error_even_though_the_quote_exists() -> None:
    """The INV-0029 failure: '04/12/2026' read as 12 April, due date proves day-first."""
    ex = extraction(
        invoice_date=f("2026-04-12", quote="04/12/2026"),
        due_date=f("2027-01-03", quote="03/01/2027"),
    )
    codes = {i.code for i in check_date_evidence(ex)}
    assert codes == {"AMBIGUOUS_DATE"}  # neither date settles the order -> human
    ex = extraction(
        invoice_date=f("2026-04-12", quote="04/12/2026"),
        due_date=f("2027-01-18", quote="18/01/2027"),
    )
    issues = check_date_evidence(ex)
    assert [i.code for i in issues] == ["DATE_EVIDENCE_MISMATCH"] and "2026-12-04" in issues[
        0
    ].message


def test_correct_and_textual_dates_pass() -> None:
    assert (
        check_date_evidence(
            extraction(
                invoice_date=f("2026-12-04", quote="04/12/2026"),
                due_date=f("2027-01-18", quote="18/01/2027"),
            )
        )
        == []
    )
    assert (
        check_date_evidence(
            extraction(invoice_date=f("2026-10-20", quote="20 Oct 2026"), due_date=f(None))
        )
        == []
    )
