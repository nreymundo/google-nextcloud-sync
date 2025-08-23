# Google → Nextcloud Sync (g2nc) Dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# System deps for building some wheels (if needed) and SSL/XML libs
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl tini build-essential libxml2 libxml2-dev libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy project metadata and source (ensures reliable editable install)
COPY pyproject.toml /app/
COPY src/ /app/src/

# Install (editable for dev image); keep --no-cache-dir for smaller layers
RUN pip install --upgrade pip && pip install --no-cache-dir -e .[dev]

# Copy the entrypoint
COPY scripts/docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Non-root user (optional)
# RUN useradd -m runner && chown -R runner:runner /app
# USER runner

# Data directory is expected to be bind-mounted at runtime
VOLUME ["/data"]

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
CMD ["g2nc", "sync", "--config", "/data/config.yaml"]