#!/usr/bin/env bash
#
# Davis WebUI — Quick Tunnel Stopper
#
# Removes the Cloudflare quick tunnel container and stops the Davis
# backend + frontend containers started by start-davis-quick-tunnel.sh.
#
# Usage:
#   ./scripts/stop-davis-quick-tunnel.sh           # stop everything
#   ./scripts/stop-davis-quick-tunnel.sh --tunnel  # stop tunnel only
#
set -euo pipefail

COMPOSE_FILE="docker-compose.davis.yml"
TUNNEL_CONTAINER="davis-quick-tunnel"

echo "=========================================="
echo "  Davis WebUI — Quick Tunnel Stopper"
echo "=========================================="
echo ""

if docker ps -a --format '{{.Names}}' | grep -q "^${TUNNEL_CONTAINER}$"; then
  echo "  Stopping + removing quick tunnel container..."
  docker rm -f "$TUNNEL_CONTAINER" >/dev/null 2>&1
  echo "  ✅ Tunnel removed"
else
  echo "  ℹ️  No quick tunnel container found (already stopped)"
fi

if [ "${1:-}" = "--tunnel" ]; then
  echo ""
  echo "  (--tunnel flag set — leaving Davis containers running)"
  echo "=========================================="
  exit 0
fi

echo ""
echo "  Stopping Davis backend + frontend containers..."
docker compose -f "$COMPOSE_FILE" down
echo "  ✅ Davis containers stopped"

echo ""
echo "=========================================="
echo "  Done. All services stopped."
echo "=========================================="
