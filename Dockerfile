# Chromium is included so browser_read / browser_act work in production.
# Build without it (about 700MB smaller) with: --build-arg INSTALL_BROWSER=0
#
# Keep this file plain ASCII and free of a VOLUME instruction: Railway manages
# the volume itself and rejects Dockerfiles that declare one.
FROM python:3.11-slim

ARG INSTALL_BROWSER=1

ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
ENV DATA_DIR=/data

# node is here for MCP servers distributed as npm packages (npx ...)
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl nodejs npm \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN if [ "$INSTALL_BROWSER" = "1" ]; then playwright install --with-deps chromium; fi

COPY . /app

# The Railway volume mounts at /data. Create it so local runs work too.
RUN mkdir -p /data

EXPOSE 8080

# One worker on purpose: the scheduler runs in-process, and two workers would
# fire every follow-up and every job slice twice.
CMD gunicorn app:app --bind 0.0.0.0:${PORT:-8080} --workers 1 --threads 8 --timeout 300 --access-logfile - --error-logfile -
