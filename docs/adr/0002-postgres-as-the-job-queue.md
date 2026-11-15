# ADR-002: Postgres as the job queue

Status: accepted · 2026-11-15

## Context

Uploads must be acknowledged quickly (202), and the LLM call (seconds, sometimes failing)
must run in the background. AP volume is low: hundreds to a few thousand invoices a month.
The free hosting tier has no managed Redis, and every extra system is one more thing to run.

## Decision

Use a `jobs` table in the same Postgres database, claimed with
`UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1)`:

- The invoice row, its file and its job are inserted **in one transaction**. There is never an
  invoice without a job, or a job without an invoice.
- Delivery is at least once. A reaper re-queues jobs whose lock is older than the timeout.
  Processing is idempotent by invoice id: finished invoices are skipped, and a restarted run
  replaces the derived rows.
- Failures back off exponentially (5 s × 2^attempts, capped at 300 s). After `max_attempts` (3)
  the job is dead-lettered and the invoice is marked `failed`. An admin endpoint re-queues it.
- `WORKER_MODE=embedded` runs the worker inside the web process (free tiers have no background
  workers). `standalone` runs `python -m ledgerlens.worker` separately.

## Consequences

- One database to back up, monitor and migrate. Enqueueing is transactional for free.
- Throughput is bounded by Postgres row locking, which is irrelevant at AP volumes.
- Polling adds up to `WORKER_POLL_S` (1 s) of latency. `LISTEN/NOTIFY` could remove it.

## Alternatives considered

- **Redis + arq/RQ:** higher throughput and a mature retry story, but a second system and no
  transaction shared with the invoice row. This is the upgrade path if volume grows 100×.
- **Celery:** heavier than needed and has the same dual-write problem.
- **Process inline in the request:** the client waits on the LLM, and a provider outage turns
  into upload failures.
