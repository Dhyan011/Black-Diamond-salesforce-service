# ============================================================
# Salesforce Service — Production Dockerfile
# Multi-stage build for minimal image size
# ============================================================

# Stage 1: Build dependencies
FROM python:3.11-slim AS builder

WORKDIR /build

# Install system dependencies for building Python packages
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Production image
FROM python:3.11-slim AS production

# Labels
LABEL maintainer="Glynac Engineering"
LABEL service="salesforce-service"
LABEL description="Salesforce Bulk API 2.0 data extraction service"

# Install runtime dependencies only
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
        && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r glynac && useradd -r -g glynac -d /app -s /sbin/nologin glynac

# Copy Python packages from builder
COPY --from=builder /install /usr/local

# Set working directory
WORKDIR /app

# Copy application code
COPY app/ ./app/

# Set ownership
RUN chown -R glynac:glynac /app

# Switch to non-root user
USER glynac

# Environment defaults (overridden by Nomad/Vault at runtime)
ENV FLASK_ENV=production \
    FLASK_DEBUG=False \
    HOST=0.0.0.0 \
    PORT=5712 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Expose the service port
EXPOSE ${PORT}

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${PORT}/api/health || exit 1

# Run with gunicorn
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5712", \
     "--workers", "2", \
     "--threads", "4", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app.main:create_app()"]
