# Stage 1: Install dependencies
FROM python:3.12-slim AS builder

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY

WORKDIR /build

COPY pyproject.toml .

RUN pip install --no-cache-dir --prefix=/install \
    "fastapi>=0.115.0" \
    "uvicorn[standard]>=0.32.0" \
    httpx>=0.27.0 \
    python-dotenv>=1.0.0 \
    pydantic>=2.0.0 \
    tushare>=1.4.0 \
    pandas>=2.1.0 \
    pyarrow

# Stage 2: Runtime
FROM python:3.12-slim

ARG USER_ID=1000
ARG GROUP_ID=1000
RUN groupadd -g ${GROUP_ID} appuser && useradd -u ${USER_ID} -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source — BOTH packages required
COPY davis_webui/ ./davis_webui/
COPY davis_analyzer/ ./davis_analyzer/

# Pre-create data directories (import-time mkdir in config.py will crash without these)
RUN mkdir -p /app/davis_webui/data/tasks \
    /app/davis_analyzer/cache \
    /app/davis_analyzer/studies && \
    chown -R appuser:appuser /app

ENV PYTHONUNBUFFERED=1

USER appuser

EXPOSE 8322

# CRITICAL: --host 127.0.0.1 NOT 0.0.0.0 — zero-auth backend must not be publicly reachable
CMD ["uvicorn", "davis_webui.backend.main:app", "--host", "127.0.0.1", "--port", "8322"]
