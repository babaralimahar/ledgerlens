# Deployment

Target from the manual (Day 16): **Render free web service** (Docker) running the API with an
embedded worker, and **Neon free Postgres** (pgvector available). The config is in
[`render.yaml`](../render.yaml).

> Status: **live** at https://ledgerlens-wjry.onrender.com (deployed 2026-11-15; Render free web
> service in Singapore + Neon free Postgres 17 in ap-southeast-1). `/readyz` -> database ok.

## Steps (about 30 minutes)

1. **Push to GitHub.** Create an empty private repo, then from `D:\AI Projects\ledgerlens` run
   `git remote add origin <url>` and `git push -u origin main --tags`. CI runs: lint, types,
   migrations, the routing gate and all tests.
2. **Neon.** Create a project, then copy the **direct** connection string (Dashboard → Connect →
   turn *Connection pooling* off). PgBouncer in transaction mode breaks asyncpg's prepared
   statements. The `postgresql://…?sslmode=require&channel_binding=require` form works as-is:
   Settings converts it for asyncpg.
3. **Render.** (Blueprints require a card on file; a plain *New → Web Service* with Docker,
   region Singapore, instance **Free**, Docker Command `sh /app/start.sh` works the same.)
   New → Blueprint → select the repo. Render reads `render.yaml`. When asked, paste:
   - `DATABASE_URL`: the Neon string from step 2
   - `LLM_API_KEY`: your Gemini key

   The first deploy builds the image, then runs `alembic upgrade head`, seeds the reference data
   and starts the server.
4. **Demo key.** From your machine, run
   `$env:DATABASE_URL="<neon url>"; uv run python -m ledgerlens.cli create-key --name demo --role clerk`.
   Keep the key for the demo. A clerk can't approve above 5,000 USD.
5. **Verify.** `curl https://<app>.onrender.com/readyz` should return 200 with
   `"database":"ok"`. Open `https://<app>.onrender.com/ui`, sign in, upload one PDF from
   `evals/datasets/invoices/pdfs/` and watch it route. Then record the cold start: the first
   request after 15+ minutes idle.

## Platform limits

| Limit | Manual's note (24 Oct 2026) | Verified on | Value you saw |
|---|---|---|---|
| Render free RAM | 512 MB | | |
| Render free idle sleep / cold start | sleeps after 15 min idle | 2026-11-15 | sleeps after 15 min; Render warns of ~50 s+ on wake |
| Render free background workers | not on free tier → `WORKER_MODE=embedded` | | |
| Neon free storage / compute | permanent free tier, scales to zero | | |

## Rollback

- **Code:** Render → Deploys → previous deploy → Rollback.
- **Schema:** each migration has a `downgrade()`. Roll back the code first, then run
  `uv run alembic downgrade -1` against Neon only if the new schema breaks the old code.

## Known limitations of the free deployment

- Cold starts: the first request after idling is slow, and the embedded worker sleeps with the
  service. Queued invoices wait until the next request wakes it.
- File bytes are stored in Postgres (`invoice_files`). Fine for a demo; use S3-compatible
  storage for real volumes.
- Gemini's free tier rate-limits requests (p95 extraction latency on dev was ~35 s because of
  retries) and Google may use free-tier content to improve its products. Use synthetic data
  only, or switch to the paid tier.
- `/metrics` is admin-only because the service is public.
