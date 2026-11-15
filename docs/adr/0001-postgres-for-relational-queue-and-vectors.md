# ADR-001: One Postgres for relational data, the job queue and vectors

Status: accepted · 2026-11-15 (inherited from ai-service-template)

## Context

Every project in the plan needs relational data with transactions, background jobs, and (from
P2 on) vector search. The budget is free tiers, operated by one person.

## Decision

Use one PostgreSQL database (with the pgvector extension) for all three:

- **Relational data:** SQLAlchemy 2.0 async, with Alembic migrations as the only way the schema
  changes.
- **Queue:** a `jobs` table claimed with `FOR UPDATE SKIP LOCKED` (details in ADR-002).
- **Vectors:** `pgvector` HNSW indexes next to the rows they describe, so permission filters are
  plain `WHERE` clauses.

## Consequences

- One system to back up, migrate, monitor and pay for (Neon's free tier covers it).
- A job and its data are written in **one transaction**, which removes the dual-write problem.
- Throughput and vector-index scale are bounded by one Postgres. That's fine for AP volumes and
  for a policy corpus of thousands of chunks; it would need revisiting at 100× scale.

## Alternatives considered

- **Redis for the queue:** faster, but a second system with no shared transaction.
- **A dedicated vector database (Pinecone, Qdrant):** better at very large scale, but it
  duplicates data and permissions and adds another bill. Postgres full-text search plus pgvector
  covers hybrid retrieval in one query.
