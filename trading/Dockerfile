# Multi-stage ultra-lean build using Python 3.11 Alpine
FROM python:3.11-alpine AS builder

WORKDIR /build

# Install build tools only for wheels if needed
RUN apk add --no-cache gcc musl-dev libffi-dev

COPY requirements.txt .

# Compile wheels without saving cache
RUN pip install --no-cache-dir --user -r requirements.txt

# Final runtime image — minimal footprint (~60MB)
FROM python:3.11-alpine

WORKDIR /app

# Set non-buffering stdout for real-time logs
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SERVER_PORT=5000 \
    SERVER_HOST=0.0.0.0 \
    PATH="/root/.local/bin:$PATH"

# Copy pre-built wheels from builder
COPY --from=builder /root/.local /root/.local

# Copy application source code
COPY . .

# Expose Web Dashboard & WebSocket port
EXPOSE 5000 5001

# Health check against the zero-dependency /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

# Start trading orchestrator
CMD ["python", "main.py"]
