# Build stage
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Production stage
FROM python:3.11-slim

WORKDIR /app

# Create non-root user
RUN useradd --create-home --shell /bin/bash app

# Copy installed packages from builder
COPY --from=builder /root/.local /home/app/.local

# Copy application code
COPY --chown=app:app src/ ./src/
COPY --chown=app:app config/ ./config/
COPY --chown=app:app scripts/ ./scripts/

# Switch to non-root user
USER app

# Add local packages to PATH
ENV PATH=/home/app/.local/bin:$PATH

# Expose port
EXPOSE 8080

# Health check — python:3.11-slim has no curl; use stdlib urllib instead.
# Endpoint is /v1/health (health_router prefix="/v1" in src/gateway/main.py).
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c 'import urllib.request; urllib.request.urlopen("http://localhost:8080/v1/health", timeout=5)' || exit 1

# Run application
CMD ["python", "-m", "uvicorn", "src.gateway.main:app", "--host", "0.0.0.0", "--port", "8080"]