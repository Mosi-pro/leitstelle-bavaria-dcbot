# Backend (FastAPI) und Discord-Bot teilen sich dieses Image.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# MySQL-/MariaDB-Client für Backup und Wiederherstellung
RUN apt-get update \
    && apt-get install -y --no-install-recommends default-mysql-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 lbc && useradd --system --uid 10001 --gid lbc --home /app lbc

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY lbc ./lbc

RUN mkdir -p /app/config /backups && chown -R lbc:lbc /app /backups
USER lbc

EXPOSE 8000
CMD ["uvicorn", "lbc.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--workers", "1"]
