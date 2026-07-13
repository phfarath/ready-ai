FROM python:3.12-slim

LABEL org.opencontainers.image.title="ready-ai"
LABEL org.opencontainers.image.description="Agentic browser automation for self-healing documentation"
LABEL org.opencontainers.image.source="https://github.com/phfarath/ready-ai"

# Install Chrome and dependencies
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    xdg-utils \
    wget \
    curl \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Set Chrome path
ENV CHROME_PATH=/usr/bin/chromium
ENV PYTHONUNBUFFERED=1

# Create non-root user for the application
RUN groupadd -r readyai && useradd -r -g readyai -d /app readyai

# Create app directory
WORKDIR /app

# Install Python dependencies first (for layer caching)
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e "." && pip install --no-cache-dir pyyaml tomli

# Copy source code
COPY --chown=readyai:readyai . .

# Install ready-ai from source
RUN pip install --no-cache-dir -e "."

# Create output directory with non-root ownership
RUN mkdir -p /app/output && chown -R readyai:readyai /app

# Switch to non-root user
USER readyai

# Expose API port
EXPOSE 8000

# Default: run API server
CMD ["ready-ai", "api", "--host", "0.0.0.0", "--port", "8000"]
