# LedgerLens eval: ledgerlens-system / dev

Dataset `18ef9ace71a03d3f…` · git `0178182-dirty` · synthetic data: real-world accuracy will differ.

## System: gemini-3.1-flash-lite / extract_v2

- **False auto-approvals: 0** (gate: 0) 
- Route accuracy: 0.960 · clean auto-approval rate: 0.941
- needs_review without a reason: 0
- False flags on clean invoices: 0 []

| Anomaly | caught | total |
|---|---|---|
| DUPLICATE_INVOICE | 3 | 3 |
| FUTURE_DATE | 2 | 2 |
| HIDDEN_TEXT | 1 | 1 |
| LINE_SUM_MISMATCH | 3 | 3 |
| MISSING_PO | 3 | 3 |
| NEEDS_OCR | 2 | 2 |
| PRICE_ABOVE_TOLERANCE | 3 | 3 |
| PROMPT_INJECTION | 3 | 3 |
| QTY_ABOVE_RECEIVED | 2 | 2 |
| TAX_MISMATCH | 3 | 3 |
| UNKNOWN_VENDOR | 3 | 3 |

| expected -> routed | n |
|---|---|
| auto_approved->auto_approved | 64 |
| auto_approved->needs_review | 4 |
| needs_review->needs_review | 29 |
| rejected->rejected | 3 |

## Extraction

- Valid output rate: 1.000 (first try 98, after repair 0, invalid 0)
- Line items: P 1.000 · R 1.000 · F1 1.000
- Cost: total $0.19219600 · mean $0.001961183673469387755102040816 · p95 $0.00258575
- Latency: p50 3418.5 ms · p95 16235 ms

| Field | accuracy |
|---|---|
| vendor_name | 1.000 |
| vendor_tax_id | 1.000 |
| invoice_number | 1.000 |
| invoice_date | 1.000 |
| due_date | 1.000 |
| po_number | 1.000 |
| subtotal | 1.000 |
| tax | 1.000 |
| total | 1.000 |
| currency | 1.000 |

