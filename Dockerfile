FROM python:3.11-slim AS builder

WORKDIR /build

COPY requirements.txt .

RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


FROM python:3.11-slim

LABEL maintainer="ipvcomp"
LABEL description="WhatsApp Bot SaaS Backend"

RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

COPY --from=builder /install /usr/local

COPY app/ ./app/
COPY main.py .

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 5000

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=staging

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/api/v1/health')" || exit 1

CMD ["gunicorn", \
     "--bind=0.0.0.0:5000", \
     "--reuse-port", \
     "--workers=4", \
     "--worker-class=uvicorn.workers.UvicornWorker", \
     "--timeout=120", \
     "--graceful-timeout=30", \
     "--keep-alive=5", \
     "--access-logfile=-", \
     "--error-logfile=-", \
     "main:app"]
