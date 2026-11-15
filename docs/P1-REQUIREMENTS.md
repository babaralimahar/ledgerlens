# P1 LedgerLens: requirements

> **Source.** Written from the manual's P1 specification (Section 07), including its
> stakeholder constraints. The manual's discovery simulation (an LLM role-playing the AP manager,
> where you uncover the constraints by asking) has **not** been run yet. Run it before presenting
> this as discovered, and record which constraints your questions surfaced.

## Problem

Northwind Supplies (simulated: 3 AP clerks, 1 AP manager, several hundred vendors) receives
vendor invoices as PDFs in hundreds of layouts. Clerks re-key about 15 fields plus line items,
then check them by hand against the PO and the goods-received note. The result is overpayments,
duplicate payments, missed early-payment discounts, and no audit trail of why something was
approved.

> "We're drowning in invoices. Can AI just read them and put them in the system? Our controller
> is nervous about AI approving payments."

## Users

| User | Needs |
|---|---|
| AP clerk | Upload invoices; see only the exceptions, with precise reasons; correct fields; decide within a limit |
| AP approver / manager | Decide anything; reopen auto-approved invoices; trust the audit trail |
| Controller | A guarantee that automation is capped and explainable |

## Constraints (from the stakeholder brief)

1. **Full audit trail of every automated decision** → insert-only `audit_events`; every status change audited.
2. **Auto-approval capped (≤ USD 5,000) and only for vendors with an existing PO** → routing policy.
3. **~10% of invoices are scans** → `NEEDS_OCR` routing, never silent failure or an empty LLM call.
4. **ERP is read-only for the pilot** → no write-back (non-goal); reference data is seeded.
5. **Bank details must not leave company systems in logs** → structlog scrubbing; raw text never logged.

## Functional requirements

| ID | Requirement | Acceptance criterion | Status |
|---|---|---|---|
| F1 | Accept a PDF, return 202 + job id; same file → same invoice | Upload twice → identical id, one row | ✅ `test_same_file_twice_…` |
| F2 | Extract header + lines with evidence quotes | Schema-valid ≥ 98% of dev after repair; quotes are substrings of page text | ✅ 100% valid on dev (gemini-3.1-flash-lite) |
| F3 | Detect arithmetic, duplicate and vendor anomalies | 100% of injected arithmetic and duplicate anomalies flagged on dev | ✅ 28/28 anomalies on dev |
| F4 | 3-way match with tolerances | ≥ 20 table-driven cases incl. boundaries | ✅ 24 cases |
| F5 | Never auto-approve a defective invoice | False auto-approvals = 0 on dev and held-out test | ✅ dev 0 · held-out: see README |
| F6 | Role-limited decisions | Clerk above limit → 403; every decision audited | ✅ API + UI tests |
| F7 | Reviewer UI | Queue, detail with reasons, decisions, upload | ✅ `/ui`, 6 tests |

## Non-functional requirements

| ID | Requirement | Acceptance criterion | Status |
|---|---|---|---|
| N1 | p95 ≤ 30 s per 2-page invoice, excluding queue wait (hypothesis) | From extraction runs + job timestamps | ⚠️ p50 4 s, p95 35 s on Gemini's **free tier** (rate-limit retries); re-measure on the paid tier |
| N2 | Worker crash recovery | Stale lock → reprocessed once, no duplicate side effects | ✅ `test_worker_crash_mid_job_recovers_once` |
| N3 | No bank account numbers in logs | Log-scrubbing test | ✅ |
| N4 | Fail safe on model or provider failure | Invalid output → review; outage → dead letter + requeue | ✅ |

## Non-goals

Payments or ERP write-back · handwritten/photographed invoices (OCR is a stretch goal) · FX
conversion · vendor onboarding/KYC · email ingestion (next step) · fine-tuning · a polished frontend.

## Risks

| Risk | Mitigation |
|---|---|
| Synthetic data overstates accuracy | Say so everywhere; next step is hand-labelled public samples |
| Day/month order misread (found on dev, 6 errors) | Date-evidence rule: ambiguous → review; contradicted → error |
| Free-tier provider limits / data use | Paid tier for anything real; spend cap in Google Cloud |
| Clerks over-trust auto-approvals | Approvers can reopen; cap and conditions shown in the UI |
