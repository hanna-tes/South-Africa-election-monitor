#!/usr/bin/env sh
set -eu

if [ -n "${DB_HOST:-}" ]; then
  python - <<'PY'
import os
import socket
import time

host = os.environ["DB_HOST"]
port = int(os.environ.get("DB_PORT", "5432"))
deadline = time.time() + int(os.environ.get("DB_WAIT_TIMEOUT", "60"))

while True:
    try:
        with socket.create_connection((host, port), timeout=2):
            break
    except OSError as exc:
        if time.time() > deadline:
            raise SystemExit(f"Database did not become reachable at {host}:{port}: {exc}")
        print(f"Waiting for database at {host}:{port}...")
        time.sleep(2)
PY
fi

if [ "${DJANGO_COLLECTSTATIC:-1}" = "1" ]; then
  python manage.py collectstatic --noinput
fi

if [ "${DJANGO_MIGRATE:-1}" = "1" ]; then
  python manage.py migrate --noinput
fi

if [ "${CLEAR_REDIS_CACHE_ON_STARTUP:-0}" = "1" ]; then
  python manage.py clear_application_cache --reason "ECS web container startup"
fi

exec "$@"
