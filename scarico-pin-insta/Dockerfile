FROM python:3.12-slim

# ffmpeg serve per i video che arrivano con audio e video separati
# (storie, alcuni reel, i pin "idea").
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ ./server/
COPY web/ ./web/

# Niente root: se qualcosa va storto, va storto con pochi privilegi.
RUN useradd --create-home --uid 10001 scarico \
 && mkdir -p /app/tmp-downloads \
 && chown -R scarico:scarico /app
USER scarico

ENV HOST=0.0.0.0 PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ['PORT']+'/api/health', timeout=4).status==200 else 1)"

CMD ["sh", "-c", "uvicorn server.main:app --host $HOST --port $PORT --proxy-headers --forwarded-allow-ips='*'"]
