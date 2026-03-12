FROM python:3.12-slim AS base

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency management
RUN pip install --no-cache-dir uv

# Copy only requirements files first for better caching
COPY apps/api/pyproject.toml ./
RUN uv pip install --system ".[dev]"

# Copy source and other necessary files
COPY apps/api/ ./
COPY apps/api/src/ ./src/
COPY prompts/ /prompts/

RUN chmod +x /app/entrypoint.sh

# Environment variables
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# --- API Stage ---
FROM base AS api
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
# Use uvicorn without --reload in production (default)
CMD ["uvicorn", "moose_api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# --- Worker Stage ---
FROM base AS worker
# Worker doesn't need entrypoint for migrations (API handles it), but needs environment
CMD ["arq", "moose_api.worker.WorkerSettings"]

