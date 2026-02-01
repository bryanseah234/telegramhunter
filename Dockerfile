FROM python:3.11-slim-bookworm

# ============================================
# Local Docker Deployment (Aggressive Mode)
# ============================================

# Environment - PYTHONOPTIMIZE=0 required for Telethon/pycparser
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONOPTIMIZE=0

WORKDIR /app

# Dependencies - minimal install
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Requirements - with cache for faster rebuilds
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && rm -rf ~/.cache/pip

# Create a non-root user
RUN useradd -m -u 1000 celery

# Application Code
COPY . .

# Make entrypoint executable
RUN chmod +x /app/docker-entrypoint.sh

# Change ownership to the new user
RUN chown -R celery:celery /app

# Switch to non-root user
USER celery

# Set entrypoint (runs CSV import before main command)
ENTRYPOINT ["/app/docker-entrypoint.sh"]

# Default command (overridden by docker-compose)
CMD ["bash"]

