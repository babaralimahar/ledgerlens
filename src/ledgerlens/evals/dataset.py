"""Load the frozen invoice dataset: ground truth, PDFs (hash-checked) and reference data."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.evals.dataset import DatasetIntegrityError, load_dataset
from ledgerlens.domain.ground_truth import InvoiceGroundTruth
from ledgerlens.domain.reference import Reference, ReferenceSet

DEFAULT_DIR = Path("evals/datasets/invoices")


@dataclass(frozen=True)
class InvoiceDataset:
    split: str
    sha256: str
    root: Path
    items: list[InvoiceGroundTruth]
    reference: Reference
    as_of: date

    def pdf_bytes(self, gt: InvoiceGroundTruth) -> bytes:
        data = (self.root / gt.pdf).read_bytes()
        if hashlib.sha256(data).hexdigest() != gt.pdf_sha256:
            raise DatasetIntegrityError(f"{gt.pdf} does not match its frozen hash")
        return data


def load_reference(root: Path = DEFAULT_DIR) -> ReferenceSet:
    return ReferenceSet.model_validate_json((root / "reference.json").read_text(encoding="utf-8"))


def load_invoices(split: str, root: Path = DEFAULT_DIR) -> InvoiceDataset:
    ds = load_dataset(root / f"{split}.jsonl", require_frozen=True)
    items = [InvoiceGroundTruth.model_validate(it.expected) for it in ds.items]
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return InvoiceDataset(
        split=split,
        sha256=ds.sha256,
        root=root,
        items=items,
        reference=Reference.from_set(load_reference(root)),
        as_of=date.fromisoformat(manifest["as_of"]),
    )
