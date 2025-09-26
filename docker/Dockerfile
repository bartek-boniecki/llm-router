# --- Stage 1: build deps into user site-packages ---
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Minimal OS packages that speed up/ensure clean pip installs
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy only requirements first for better layer caching
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# --- Stage 2: final runtime image ---
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH="/root/.local/bin:${PATH}"

# Copy application code
COPY . .

# Expose API port
EXPOSE 8000

# IMPORTANT: launch through the safe runner, not uvicorn directly
# The safe runner prints clear tracebacks and keeps the process alive on failure,
# so `docker compose logs -f api` always shows what went wrong.
CMD ["python", "-m", "api.run"]
