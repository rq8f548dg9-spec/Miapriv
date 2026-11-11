#!/usr/bin/env bash
# Avvio su macOS e Linux. Prepara tutto la prima volta, poi parte e basta.
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}

if [ ! -f .env ]; then
  echo "Primo avvio: creo il file .env"
  cp .env.example .env
  CHIAVE=$("$PY" -c "import secrets; print(secrets.token_urlsafe(48))")
  "$PY" - "$CHIAVE" <<'EOF'
import pathlib, sys
percorso = pathlib.Path(".env")
testo = percorso.read_text(encoding="utf-8").replace("SECRET_KEY=", "SECRET_KEY=" + sys.argv[1], 1)
percorso.write_text(testo, encoding="utf-8")
EOF
  echo
  echo "  Apri .env e scrivi la tua APP_PASSWORD, poi rilancia questo script."
  echo
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Creo l'ambiente Python e installo le dipendenze (solo la prima volta)..."
  "$PY" -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -r requirements.txt
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Attenzione: ffmpeg non e' installato. I video con audio separato non funzioneranno."
  echo "  macOS:  brew install ffmpeg"
  echo "  Debian: sudo apt install ffmpeg"
fi

set -a; . ./.env; set +a
echo "In ascolto su http://${HOST:-0.0.0.0}:${PORT:-8080}"
exec .venv/bin/uvicorn server.main:app \
  --host "${HOST:-0.0.0.0}" --port "${PORT:-8080}" \
  --proxy-headers --forwarded-allow-ips='*'
