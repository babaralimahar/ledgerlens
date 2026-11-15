# Held-out test set runs

Every run of a frozen test split is recorded here. More than one row for the same
suite and dataset hash means the set was re-run (forced) and must be disclosed.

| UTC time | suite | split | dataset sha256 | git | forced |
|---|---|---|---|---|---|

## Disclosures

- 2026-11-15: while building the generator, a sanity probe ran the deterministic rules on the
  **ground truth** of both splits (oracle extraction, no model) to check that every printed value
  is findable in the PDF text and that labels match routing. No rule, threshold or prompt was
  changed after seeing the test output. The probe is not a model evaluation; the first real
  held-out run, with a model, will be logged in the table above.
