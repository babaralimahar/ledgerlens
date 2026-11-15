# LedgerLens eval: ledgerlens-system / test

Dataset `960063ae4f82b79d…` · git `0178182-dirty` · synthetic data: real-world accuracy will differ.

## System: gemini-3.1-flash-lite / extract_v2

- **False auto-approvals: 0** (gate: 0) 
- Route accuracy: 0.940 · clean auto-approval rate: 0.921
- needs_review without a reason: 0
- False flags on clean invoices: 0 []

| Anomaly | caught | total |
|---|---|---|
| ABSURD_TOTAL | 1 | 1 |
| DUPLICATE_INVOICE | 1 | 1 |
| FUTURE_DATE | 1 | 1 |
| LINE_SUM_MISMATCH | 1 | 1 |
| MISSING_PO | 1 | 1 |
| NEEDS_OCR | 1 | 1 |
| PRICE_ABOVE_TOLERANCE | 1 | 1 |
| PROMPT_INJECTION | 1 | 1 |
| QTY_ABOVE_RECEIVED | 1 | 1 |
| TAX_MISMATCH | 1 | 1 |
| UNKNOWN_VENDOR | 1 | 1 |

| expected -> routed | n |
|---|---|
| auto_approved->auto_approved | 35 |
| auto_approved->needs_review | 3 |
| needs_review->needs_review | 11 |
| rejected->rejected | 1 |

## Extraction

- Valid output rate: 1.000 (first try 49, after repair 0, invalid 0)
- Line items: P 1.000 · R 1.000 · F1 1.000
- Cost: total $0.09330450 · mean $0.001904173469387755102040816327 · p95 $0.002592
- Latency: p50 3379 ms · p95 34712 ms

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

