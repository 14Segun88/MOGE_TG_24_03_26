# ── Этап 1: зависимости ────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Копируем только requirements для кеширования слоя
COPY requirements.txt .

RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Этап 2: финальный образ ────────────────────────────────────────────────────
FROM python:3.12-slim

# Системные зависимости lxml (libxml2, libxslt)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 \
        libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем установленные пакеты из этапа builder
COPY --from=builder /install /usr/local

# Копируем исходный код, маппинг и XSD-схемы
COPY app/ ./app/
COPY run.py .
COPY mapping_PZ_ZnP.json .
COPY mapping_PZ_xsd_xsl.json .
COPY "xsd and xsl"/ ./"xsd and xsl"/

# Переменные окружения по умолчанию (переопределяются через docker-compose или -e)
ENV HOST=0.0.0.0 \
    PORT=8000 \
    RELOAD=false \
    LOG_LEVEL=info \
    MAPPING_FILE_PATH=/app/mapping_PZ_ZnP.json \
    MAX_XML_SIZE_BYTES=52428800 \
    MAX_MAPPING_SIZE_BYTES=10485760

EXPOSE 8000

# Healthcheck — проверяет /api/v1/health каждые 30 секунд
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

CMD ["python", "run.py"]
