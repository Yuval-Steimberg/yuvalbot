#!/bin/sh
# Start the web process.
#
# The expansion of PORT happens HERE, inside the script, because a platform may
# run the start command without a shell — which is how gunicorn ends up being
# handed the literal string '$PORT' and refusing to start.
set -e
PORT="${PORT:-8080}"
echo "starting gunicorn on 0.0.0.0:${PORT}"
exec gunicorn app:app \
  --bind "0.0.0.0:${PORT}" \
  --workers 1 \
  --threads 8 \
  --timeout 300 \
  --access-logfile - \
  --error-logfile -
