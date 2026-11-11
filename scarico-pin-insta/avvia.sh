#!/usr/bin/env bash
# Avvio in locale (Mac, Linux, Raspberry). Per Hugging Face non serve: vedi README.
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}

if [ ! -f .env ]; then
  cp .env.example .env
  CHIAVE=$("$PY" -c "import secrets; print(secrets.token_urlsafe(24))")
  "$PY" - "$CHIAVE" <<'EOF'
import pathlib, sys
p = pathlib.Path(".env")
p.write_text(p.read_text(encoding="utf-8").replace("ACCESS_KEY=", "ACCESS_KEY=" + sys.argv[1], 1), encoding="utf-8")
EOF
  echo "Ho creato .env con una chiave nuova."
fi

if [ ! -d .venv ]; then
  echo "Installo le dipendenze (solo la prima volta)..."
  "$PY" -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi

command -v ffmpeg >/dev/null 2>&1 || echo "Attenzione: ffmpeg non installato, i video con audio separato falliranno."

set -a; . ./.env; set +a
echo
echo "  Apri:  http://localhost:${PORT:-8080}/?k=${ACCESS_KEY}"
echo
exec .venv/bin/uvicorn server.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8080}" \
  --proxy-headers --forwarded-allow-ips='*'
