"""Small builders for hand-written test cases."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ledgerlens.domain.reference import POLine, PurchaseOrder, Reference, Vendor
from ledgerlens.domain.schemas import InvoiceExtraction

VENDOR = Vendor(
    id="ven_001",
    name="Acme Industrial Supply",
    tax_id="TX-11112222",
    default_currency="USD",
    tax_rate=Decimal("0.1"),
)
PO = PurchaseOrder(
    po_number="PO-2026-1001",
    vendor_id="ven_001",
    currency="USD",
    lines=(
        POLine(
            line_no=1,
            sku="SKU-1000",
            description="Steel bolts M8 box of 100",
            qty=Decimal(10),
            unit_price=Decimal("10.00"),
            qty_received=Decimal(10),
        ),
        POLine(
            line_no=2,
            sku="SKU-2000",
            description="Safety gloves size L",
            qty=Decimal(5),
            unit_price=Decimal("100.00"),
            qty_received=Decimal(4),
        ),
    ),
)
REF = Reference(vendors=[VENDOR], pos={"PO20261001": PO})


def f(value: Any, quote: str | None = None, conf: float = 0.95) -> dict[str, Any]:
    if value is None:
        return {"value": None, "evidence": None, "confidence": 1.0}
    return {
        "value": value,
        "evidence": {"quote": quote or str(value), "page": 1},
        "confidence": conf,
    }


def line(desc: str, qty: str, price: str, amount: str, ref: str | None = None) -> dict[str, Any]:
    return {
        "description": f(desc),
        "quantity": f(qty),
        "unit_price": f(price),
        "amount": f(amount),
        "po_line_ref": f(ref),
    }


def extraction(**over: Any) -> InvoiceExtraction:
    """A clean invoice against PO above: 10 x 10.00 + 4 x 100.00 = 500.00, tax 10 %."""
    data: dict[str, Any] = {
        "vendor_name": f("Acme Industrial Supply"),
        "vendor_tax_id": f("TX-11112222"),
        "invoice_number": f("INV-2026-0042"),
        "invoice_date": f("2026-10-20"),
        "due_date": f("2026-11-19"),
        "po_number": f("PO-2026-1001"),
        "subtotal": f("500.00"),
        "tax": f("50.00"),
        "total": f("550.00"),
        "currency": f("USD"),
        "lines": [
            line("Steel bolts M8 box of 100", "10", "10.00", "100.00", "L1"),
            line("Safety gloves size L", "4", "100.00", "400.00", "L2"),
        ],
    }
    data.update(over)
    return InvoiceExtraction.model_validate(data)
