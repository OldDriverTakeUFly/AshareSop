#!/usr/bin/env bash
#
# Davis WebUI — Quick Tunnel Launcher (No Account Required)
#
# Starts backend + frontend Docker containers, then launches a Cloudflare
# quick tunnel that gives you a public HTTPS URL for mobile access.
#
# The URL is temporary (changes on each run) and has NO authentication.
# Use this for testing. For production, set up a named tunnel with
# Cloudflare Access (see scripts/verify-davis-tunnel.sh).
#
# Usage:
#   ./scripts/start-davis-quick-tunnel.sh          # start
#   ./scripts/stop-davis-quick-tunnel.sh           # stop (or Ctrl+C)
#
set -euo pipefail

COMPOSE_FILE="docker-compose.davis.yml"
TUNNEL_CONTAINER="davis-quick-tunnel"

echo "=========================================="
echo "  Davis WebUI — Quick Tunnel Launcher"
echo "=========================================="
echo ""

echo "[1/3] Starting backend + frontend containers..."
docker compose -f "$COMPOSE_FILE" up -d --no-build davis-backend davis-frontend
echo "  Waiting 10s for services to be healthy..."
sleep 10

if ! curl -sf http://localhost:3100/api/health >/dev/null 2>&1; then
  echo "  ❌ Backend/frontend not responding on :3100"
  echo "     Check: docker compose -f $COMPOSE_FILE logs"
  exit 1
fi
echo "  ✅ Services healthy"

echo ""
echo "[2/3] Preparing tunnel..."
if docker ps -a --format '{{.Names}}' | grep -q "^${TUNNEL_CONTAINER}$"; then
  echo "  Removing existing quick tunnel container..."
  docker rm -f "$TUNNEL_CONTAINER" >/dev/null 2>&1
fi

echo ""
echo "[3/3] Launching Cloudflare quick tunnel..."
echo "  (Press Ctrl+C to stop. The public URL will be invalidated.)"
echo ""
echo "=========================================="
echo "  ⏳ Waiting for tunnel URL..."
echo "  It will appear below in a few seconds."
echo "=========================================="
echo ""

exec docker run --rm --name "$TUNNEL_CONTAINER" --network host \
  cloudflare/cloudflared:latest \
  tunnel --no-autoupdate --url http://localhost:3100
