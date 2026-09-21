# Chromium is included so browser_read / browser_act work in production.
# Build without it (≈700MB smaller) with: --build-arg INSTALL_BROWSER=0
FROM python:3.11-slim

ARG INSTALL_BROWSER=1
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
    DATA_DIR=/data

# node is here for MCP servers distributed as npm packages (npx ...)
RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates curl nodejs npm && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt && \
    if [ "$INSTALL_BROWSER" = "1" ]; then playwright install --with-deps chromium; fi

COPY . .

# The volume mounts here; without it the agent forgets everything on redeploy.
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8080
# One worker on purpose: the scheduler runs in-process, and two workers would
# fire every follow-up twice.
CMD gunicorn app:app --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 8 \
    --timeout 300 --access-logfile - --error-logfile -
