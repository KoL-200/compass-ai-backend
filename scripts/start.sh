#!/bin/sh
set -e

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
  n=0
  until alembic upgrade head; do
    n=$((n+1))
    if [ "$n" -ge 5 ]; then
      echo "Migrations failed after 5 attempts"
      exit 1
    fi
    echo "Migration attempt $n failed; retrying in 5 seconds..."
    sleep 5
  done
fi

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers "${WEB_CONCURRENCY:-1}" \
  --proxy-headers \
  --forwarded-allow-ips "*" \
  --no-access-log