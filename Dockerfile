# binance-ai-future — image produksi (server web + core trading)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

# Folder runtime (state risiko, jurnal, pilihan AI) — pasang volume agar persisten
# Proses berjalan sebagai user non-root.
RUN mkdir -p data logs \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser
VOLUME ["/app/data", "/app/logs"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)" || exit 1

# Konfigurasi dibaca dari environment / file .env yang di-mount (JANGAN
# membakar secret ke dalam image). Set WEB_API_TOKEN di environment!
CMD ["python", "run.py", "web"]
