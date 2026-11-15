# Deployment

Target from the manual (Day 5): **Render free web service** (Docker) + **Neon free Postgres** with
pgvector. Optional: a ~USD 5–6/month VPS running `docker compose` behind Caddy.

> Free-tier limits change often. The "verified" column must be filled in from the providers'
> pricing pages **on the day you deploy**. Do not copy numbers from memory or from this file's
> history.

## Platform limits

| Limit | Manual's note (24 Oct 2026) | Verified on (date) | Value you verified |
|---|---|---|---|
| Render free RAM | 512 MB | | |
| Render free idle sleep / cold start | sleeps after 15 min idle | | cold start measured: ___ s |
| Render free background workers | generally not on free tier → `WORKER_MODE=embedded` | | |
| Render free pre-deploy command | (check) | | |
| Neon free storage / compute | free Postgres that scales to zero | | |
| Neon pgvector | available (`CREATE EXTENSION vector`) | | |

## Steps

1. **Neon:** create a project and database. Copy the **direct** connection string (not the
   `-pooler` host: PgBouncer in transaction mode breaks asyncpg's prepared statements). A
   `postgresql://…?sslmode=require` URL works as-is: Settings converts it for asyncpg.
2. **Migrations (once per release, never on every boot):** from your machine,
   `$env:DATABASE_URL="<neon url>"; uv run alembic upgrade head`. If Render's free plan offers a
   pre-deploy command, use `alembic upgrade head` there instead.
3. **Render:** New → Web Service → this repo → runtime Docker. Health check path `/healthz`.
   Environment variables (set in the dashboard, never in git):
   `DATABASE_URL`, `ENV=prod`, `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`,
   `WORKER_MODE=embedded`. Render sets `PORT`, and the Dockerfile honours it.
4. **First API key:** `$env:DATABASE_URL="<neon url>"; uv run python -m app.cli create-key --name you --role admin`.
5. **Verify:** `curl https://<app>.onrender.com/readyz` returns 200 with `"database":"ok"`.
   Record the cold-start time (first request after more than 15 minutes idle) in the table above.

## Rollback

- Code: Render → Deploys → pick the previous deploy → Rollback.
- Schema: each migration has a `downgrade()`. Roll back code first, then
  `uv run alembic downgrade -1` only if the new schema breaks the old code.

## Known limitations (state these honestly in the README)

- Cold starts on the free tier: the first request after idling is slow.
- The embedded worker sleeps with the web service, so queued jobs wait until the next request
  wakes it up.
- `/metrics` is admin-only because the service is public. A Prometheus scraper would need an
  admin key header.
