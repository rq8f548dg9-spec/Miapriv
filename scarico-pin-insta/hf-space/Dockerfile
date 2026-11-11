# Hugging Face Spaces, SDK docker. La porta e' quella dichiarata nel README.
FROM python:3.12-slim

# ffmpeg serve per i video che arrivano con audio e video separati
# (storie, alcuni reel, i pin "idea").
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Su Spaces il processo gira come utente 1000: scrivere altrove non e' permesso.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH PYTHONUNBUFFERED=1

WORKDIR /home/user/app
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt
COPY --chown=user app.py .

EXPOSE 7860
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860", \
     "--proxy-headers", "--forwarded-allow-ips=*"]
