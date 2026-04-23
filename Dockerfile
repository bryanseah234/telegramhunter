# ============================================
# Stage 1: base — system deps + Python packages
# All services share this cached layer.
# Only rebuilds when requirements.txt changes.
# ============================================
FROM python:3.11-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONOPTIMIZE=0 \
    PYTHONPATH=/app

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    dos2unix \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Python dependencies — cached separately from app code
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && rm -rf ~/.cache/pip

# Non-root user
RUN useradd -m -u 1000 celery

# ============================================
# Stage 2: final — app code only
# Rebuilds fast: only copies source files.
# ============================================
FROM base AS final

# Application code
COPY . .

# Required directories + ownership
RUN mkdir -p /app/imports/processed && \
    chown -R celery:celery /app

# Fix line endings (Windows CRLF → LF) and make entrypoint executable
RUN dos2unix /app/docker-entrypoint.sh && chmod +x /app/docker-entrypoint.sh

USER celery

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["bash"]
