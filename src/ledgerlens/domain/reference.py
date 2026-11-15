"""Reference data (vendor master, POs, goods receipts) and an in-memory lookup.

The ERP is read-only for the pilot: these records are seeded, never written by LedgerLens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from ledgerlens.domain.normalize import norm_id

Currency = str  # ISO 4217 code; FX conversion is a non-goal


class Vendor(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    name: str
    tax_id: str
    default_currency: Currency
    tax_rate: Decimal  # e.g. 0.085 for 8.5 %


class POLine(BaseModel):
    model_config = ConfigDict(frozen=True)
    line_no: int
    sku: str
    description: str
    qty: Decimal
    unit_price: Decimal
    qty_received: Decimal  # sum of goods receipts for this line


class PurchaseOrder(BaseModel):
    model_config = ConfigDict(frozen=True)
    po_number: str
    vendor_id: str
    currency: Currency
    lines: tuple[POLine, ...]


class ReferenceSet(BaseModel):
    """Serializable bundle used by the generator, the seed command and offline evals."""

    vendors: list[Vendor]
    purchase_orders: list[PurchaseOrder]


@dataclass
class Reference:
    """Lookup used by the pure decision functions."""

    vendors: list[Vendor]
    pos: dict[str, PurchaseOrder] = field(default_factory=dict)

    @classmethod
    def from_set(cls, ref: ReferenceSet) -> Reference:
        return cls(
            vendors=list(ref.vendors),
            pos={norm_id(po.po_number): po for po in ref.purchase_orders},
        )

    def vendor_by_id(self, vendor_id: str) -> Vendor | None:
        return next((v for v in self.vendors if v.id == vendor_id), None)

    def po(self, po_number: str | None) -> PurchaseOrder | None:
        return self.pos.get(norm_id(po_number)) if po_number else None
