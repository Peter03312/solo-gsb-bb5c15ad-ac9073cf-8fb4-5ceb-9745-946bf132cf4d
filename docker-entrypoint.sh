#!/bin/sh
set -e

# Create / initialize the SQLite schema (including built-in allergens) before
# gunicorn starts accepting requests.
python - <<'PY'
from db import init_db
init_db()
print("database initialized at", __import__("os").environ.get("DB_PATH"))
PY

exec gunicorn \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers 2 \
    --threads 4 \
    --timeout 60 \
    app:application
