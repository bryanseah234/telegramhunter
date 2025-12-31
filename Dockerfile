FROM python:3.11-slim-bookworm

# Environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Dependencies
# Install system deps if needed (e.g. gcc, libpq-dev for psycopg2 if binary not used)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create a non-root user
RUN useradd -m -u 1000 celery

# Application Code
COPY . .

# Change ownership to the new user
RUN chown -R celery:celery /app

# Switch to non-root user
USER celery

# Default command (can be overridden)
CMD ["bash"]
