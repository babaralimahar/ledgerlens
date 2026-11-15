# ADR-003: Deterministic pipeline with one LLM step, not an agent

Status: accepted · 2026-11-15

## Context

Approving an invoice moves money. The controller wants a full audit trail and a hard cap on
automatic approvals. The steps are known in advance: read, extract, validate, match, route.
Only reading many vendor layouts is genuinely hard for code.

## Decision

- The LLM is used for **one** step: filling `InvoiceExtraction` (a Pydantic schema). It has no
  tools and cannot change state. Its output is validated twice (provider schema and Pydantic),
  repaired at most once, and every value must carry an evidence quote found in the PDF text.
- Everything that decides is pure Python with tests: arithmetic and tax, dates, vendor match
  (exact tax id, else rapidfuzz ≥ 90), duplicates (unique dedupe key), the 3-way match with
  tolerances, and the routing policy.
- Routing fails safe. Auto-approval requires every condition to hold, and any missing or
  uncertain fact becomes a reason for review.

## Consequences

- Every decision can be explained from stored reasons, and every rule is unit-tested. The
  decision table covers 24 cases, including exact tolerance boundaries.
- Prompt injection can at worst corrupt extracted values, and those still have to pass evidence
  and arithmetic checks. It cannot approve anything. Instruction-like text and hidden text are
  also flagged for a human.
- New invoice types need rule changes in code, not prompt changes. That is the intended
  trade-off.

## Alternatives considered

- **A tool-using agent that decides approval:** non-deterministic, hard to audit, and it widens
  the injection blast radius from "wrong field" to "wrong payment".
- **Template or regex extraction per vendor:** deterministic, but it breaks whenever a vendor
  changes layout, and it costs maintenance per vendor.
