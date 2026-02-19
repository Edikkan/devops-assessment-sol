# ──────────────────────────────────────────────────────────────────────────────
# DevOps Assessment — Python / FastAPI (Optimized)
#
# Optimizations applied:
#   - Multi-stage build (smaller final image)
#   - Non-root user for security
#   - Multiple uvicorn workers for better concurrency
#   - Optimized layer caching
# ──────────────────────────────────────────────────────────────────────────────

# Stage 1: Builder
FROM python:3.11-slim as builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Stage 2: Runtime
FROM python:3.11-slim

# Create non-root user for security
RUN groupadd -r appgroup && useradd -r -g appgroup appuser

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Set working directory
WORKDIR /app

# Copy application code
COPY --chown=appuser:appgroup main.py .

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')" || exit 1

# Run with multiple workers for better concurrency
# Workers = 2 * CPU cores + 1 (for 1 CPU in container = 3 workers)
# Using Uvicorn workers with Gunicorn for production-grade serving
CMD ["gunicorn", "main:app", "--workers", "4", "--worker-class", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "--keep-alive", "5", "--timeout", "120", "--graceful-timeout", "30"]
