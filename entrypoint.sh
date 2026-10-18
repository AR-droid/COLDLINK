#!/bin/sh
set -e

# Start FalkorDB (same binary/entrypoint the official image uses) in the background.
# BROWSER=0 skips the bundled Next.js Browser UI, which would otherwise also
# bind $PORT and collide with uvicorn below.
BROWSER=0 /var/lib/falkordb/bin/run.sh &

echo "Waiting for FalkorDB..."
until redis-cli -h localhost -p 6379 ping >/dev/null 2>&1; do
  sleep 1
done
echo "FalkorDB is up."

if [ ! -f data/srfl.json ]; then
  python3 -m coldlink.generate
fi
python3 -m coldlink.load

exec python3 -m uvicorn coldlink.api:app --host 0.0.0.0 --port "${PORT:-8080}"
