FROM python:3.11-slim-bookworm

# ============================================
# Railway Free Tier Optimization
# ============================================

# Environment - Memory optimization
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONOPTIMIZE=2 \
    MALLOC_ARENA_MAX=2

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

# Change ownership to the new user
RUN chown -R celery:celery /app

# Switch to non-root user
USER celery

# Default command (can be overridden by Procfile)
CMD ["bash"]
