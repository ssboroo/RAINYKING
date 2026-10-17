#!/bin/bash
set -euo pipefail
export PORT="${PORT:-8080}"
envsubst '${PORT}' < /app/deploy/nginx.conf.template > /etc/nginx/nginx.conf
pids=()
cleanup() { kill -TERM "${pids[@]}" 2>/dev/null || true; wait || true; }
trap cleanup EXIT
trap 'exit 0' TERM INT
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
pids+=("$!")
HOSTNAME=127.0.0.1 PORT=3000 node /app/web/server.js &
pids+=("$!")
nginx -g 'daemon off;' &
pids+=("$!")
set +e
wait -n "${pids[@]}"
status=$?
set -e
# A required process exiting, even cleanly, must restart the whole service.
if [ "$status" -eq 0 ]; then status=1; fi
exit "$status"
