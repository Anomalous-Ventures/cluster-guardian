# Cluster Guardian - Agentic AI for Kubernetes Self-Healing
# Multi-stage build for smaller image

# =============================================================================
# Frontend Build Stage
# =============================================================================

FROM node:20-alpine AS frontend-builder

WORKDIR /frontend

COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# =============================================================================
# Python Builder Stage
# =============================================================================

FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# =============================================================================
# Runtime Stage
# =============================================================================

FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 guardian

# Copy Python packages from builder
COPY --from=builder /root/.local /home/guardian/.local
RUN chown -R guardian:guardian /home/guardian/.local

# Copy application code
COPY src/ ./src/

# Copy built frontend
COPY --from=frontend-builder /frontend/dist ./frontend/dist

# Set ownership
RUN chown -R guardian:guardian /app

USER guardian

# Add local bin to PATH
ENV PATH=/home/guardian/.local/bin:$PATH

# Environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CLUSTER_GUARDIAN_HOST=0.0.0.0 \
    CLUSTER_GUARDIAN_PORT=8900

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8900/health || exit 1

EXPOSE 8900

CMD ["python", "-m", "uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8900"]
