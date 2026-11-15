# Held-out test set runs

Every run of a frozen test split is recorded here. More than one row for the same
suite and dataset hash means the set was re-run (forced) and must be disclosed.

| UTC time | suite | split | dataset sha256 | git | forced |
|---|---|---|---|---|---|
| 2026-11-15T06:52:41+00:00 | ledgerlens-system | test | 960063ae4f82b79d7f45e9f46531142a2a657da5e9750dabb872b13d4bc05ecb | 0178182-dirty | no |

Result of that run (gemini-3.1-flash-lite, prompt extract_v2):
`evals/reports/ledgerlens-system-test-20261115-065241-0178182-dirty.{json,md}`.
The git field says `-dirty` because the run used the then-uncommitted code that was committed
straight afterwards as P1 v1.0.0.

## Disclosures

- 2026-11-15: while building the generator, a sanity probe ran the deterministic rules on the
  **ground truth** of both splits (oracle extraction, no model) to check that every printed value
  is findable in the PDF text and that labels match routing. No rule, threshold or prompt was
  changed after seeing the test output. The probe was not a model evaluation; the single
  model run is the row in the table above.
