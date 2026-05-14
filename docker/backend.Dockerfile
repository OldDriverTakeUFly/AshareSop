# Stage 1: Install dependencies
FROM python:3.12-slim AS builder

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY

WORKDIR /build

COPY pyproject.toml .

RUN pip install --no-cache-dir --prefix=/install \
    requests>=2.31.0 \
    pandas>=2.1.0 \
    sqlite-utils>=3.35 \
    Pillow>=10.2.0 \
    APScheduler>=3.10.4 \
    openai>=1.12.0 \
    python-dateutil>=2.8.2 \
    loguru>=0.7.2 \
    python-dotenv>=1.0.0 \
    akshare>=1.14.0 \
    "fastapi>=0.115.0" \
    "uvicorn[standard]>=0.32.0" \
    aiosqlite>=0.20.0 \
    httpx>=0.27.0

# Stage 2: Runtime
FROM python:3.12-slim

ARG USER_ID=1000
ARG GROUP_ID=1000
RUN groupadd -g ${GROUP_ID} appuser && useradd -u ${USER_ID} -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY stockhot/ ./stockhot/

# Create storage dirs (appuser-writable)
RUN mkdir -p /app/storage/database /app/storage/files/images /app/storage/files/reports /app/data && \
    chown -R appuser:appuser /app

# Environment defaults
ENV PROJECT_ROOT=/app
ENV PYTHONUNBUFFERED=1

USER appuser

EXPOSE 8321

CMD ["uvicorn", "stockhot.api.main:app", "--host", "0.0.0.0", "--port", "8321"]
