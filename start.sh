#!/bin/sh
# Container start for single-service hosts (Render free has no pre-deploy hook):
# migrate and seed (both idempotent), then serve.
set -e
alembic upgrade head
python -m ledgerlens.cli seed
exec uvicorn ledgerlens.main:create_app --factory --host 0.0.0.0 --port "${PORT:-8000}" \
  --no-access-log --timeout-graceful-shutdown 20
