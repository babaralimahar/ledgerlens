# Synthetic invoice dataset (seed 42, as of 2026-11-02)

Synthetic data: real-world accuracy will differ. Counts per label (an invoice can carry two labels).

| Label | dev | test |
|---|---|---|
| ABSURD_TOTAL | 0 | 1 |
| DUPLICATE_INVOICE | 3 | 1 |
| FUTURE_DATE | 2 | 1 |
| HIDDEN_TEXT | 1 | 0 |
| LINE_SUM_MISMATCH | 3 | 1 |
| MISSING_PO | 3 | 1 |
| NEEDS_OCR | 2 | 1 |
| PRICE_ABOVE_TOLERANCE | 3 | 1 |
| PROMPT_INJECTION | 3 | 1 |
| QTY_ABOVE_RECEIVED | 2 | 1 |
| TAX_MISMATCH | 3 | 1 |
| TOTAL | 100 | 50 |
| UNKNOWN_VENDOR | 3 | 1 |
| clean | 73 | 39 |
| route:auto_approved | 68 | 38 |
| route:needs_review | 29 | 11 |
| route:rejected | 3 | 1 |
