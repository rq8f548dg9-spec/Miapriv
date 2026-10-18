#!/usr/bin/env bash
# Avvia il pannello di controllo. Al primo lancio prepara tutto da solo.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 non trovato. Installalo da https://www.python.org/downloads/ e riprova."
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Prima configurazione: preparo l'ambiente (un minuto circa)..."
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -e .
fi

[ -f .env ] || cp .env.example .env

echo "Apro il pannello nel browser..."
exec ./.venv/bin/python -m main --ui
