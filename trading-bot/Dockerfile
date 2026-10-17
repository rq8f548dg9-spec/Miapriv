# Immagine del bot. Niente credenziali qui dentro: arrivano dall'ambiente.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
      "pydantic>=2.6" "pyyaml>=6.0" "websockets>=12.0" "aiohttp>=3.9" \
      "polars>=0.20" "duckdb>=0.10" "pyarrow>=15.0" "structlog>=24.1"

COPY . .

# Utente non privilegiato: un bot che gira come root e' un bot che puo'
# fare molto piu' danno di quello per cui e' stato scritto.
RUN useradd --create-home --uid 10001 bot \
    && mkdir -p /app/logs /app/state /app/data_store \
    && chown -R bot:bot /app
USER bot

# Controllo di sanita': la configurazione deve restare valida.
HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
    CMD python -m main --check || exit 1

CMD ["python", "-m", "main"]
