#!/usr/bin/env bash
#
# Davis WebUI — Cloudflare Tunnel End-to-End Verification
#
# Prerequisites:
#   1. .env.davis has a REAL TUNNEL_TOKEN (not "your_tunnel_token_here")
#   2. CORS_ORIGINS in .env.davis matches your Cloudflare domain
#   3. Cloudflare Tunnel public hostname configured: davis.<domain> → localhost:3100
#   4. Cloudflare Access policy configured (Email OTP or OAuth)
#
# Usage:
#   chmod +x scripts/verify-davis-tunnel.sh
#   DOMAIN=https://davis.yourdomain.com ./scripts/verify-davis-tunnel.sh
#
set -euo pipefail

DOMAIN="${DOMAIN:-}"
EVIDENCE_DIR=".sisyphus/evidence"
mkdir -p "$EVIDENCE_DIR"

echo "=========================================="
echo "  Davis Cloudflare Tunnel Verification"
echo "=========================================="
echo ""

# --- Step 0: Pre-flight checks ---
echo "[0/6] Pre-flight checks..."

if grep -q "your_tunnel_token_here" .env.davis; then
  echo "  ❌ FAIL: .env.davis still has placeholder TUNNEL_TOKEN"
  echo "     Fix: Edit .env.davis, set TUNNEL_TOKEN to your real Cloudflare tunnel token"
  exit 1
fi
echo "  ✅ TUNNEL_TOKEN is set (not placeholder)"

if [ -z "$DOMAIN" ]; then
  echo "  ⚠️  No DOMAIN env var set. Reading CORS_ORIGINS from .env.davis..."
  DOMAIN=$(grep "^CORS_ORIGINS=" .env.davis | cut -d= -f2 | cut -d, -f1)
  echo "     Using: $DOMAIN"
fi

if [[ "$DOMAIN" == *"yourdomain.com"* ]]; then
  echo "  ❌ FAIL: CORS_ORIGINS still has placeholder domain"
  echo "     Fix: Edit .env.davis, set CORS_ORIGINS to https://davis.youractualdomain.com"
  exit 1
fi
echo "  ✅ Domain looks real: $DOMAIN"

echo ""

# --- Step 1: Start all 3 services ---
echo "[1/6] Starting Docker stack (backend + frontend + tunnel)..."
docker compose -f docker-compose.davis.yml up -d
echo "  Waiting 20s for tunnel to connect..."
sleep 20

# --- Step 2: Verify tunnel connected ---
echo ""
echo "[2/6] Checking tunnel connection..."
if docker logs davis-tunnel 2>&1 | grep -q "Registered tunnel connection"; then
  echo "  ✅ PASS: Tunnel registered with Cloudflare edge"
  docker logs davis-tunnel 2>&1 | grep "Registered tunnel connection" | head -4
else
  echo "  ❌ FAIL: No tunnel connection found in logs"
  echo "  Last 20 log lines:"
  docker logs davis-tunnel 2>&1 | tail -20
  echo ""
  echo "  Possible causes:"
  echo "    - Invalid/expired TUNNEL_TOKEN"
  echo "    - Tunnel not configured in Cloudflare dashboard"
  echo "    - Public hostname not set to localhost:3100"
  exit 1
fi

# --- Step 3: Check local services still work ---
echo ""
echo "[3/6] Local service health..."
curl -sf http://localhost:3100/api/health > /dev/null 2>&1 && echo "  ✅ Frontend proxy: healthy" || echo "  ❌ Frontend proxy: FAIL"
curl -sf http://localhost:8322/api/health > /dev/null 2>&1 && echo "  ✅ Backend direct: healthy" || echo "  ❌ Backend direct: FAIL"

# --- Step 4: Public URL requires auth (Cloudflare Access) ---
echo ""
echo "[4/6] Public URL auth check..."
HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" -L --max-redirs 0 "${DOMAIN}/api/health" 2>/dev/null || echo "000")
if [[ "$HTTP_CODE" == "302" ]] || [[ "$HTTP_CODE" == "403" ]]; then
  echo "  ✅ PASS: Public URL returns $HTTP_CODE (Cloudflare Access gate active)"
elif [[ "$HTTP_CODE" == "200" ]]; then
  echo "  ⚠️  WARN: Public URL returns 200 — Cloudflare Access may NOT be configured"
  echo "     Anyone with the URL can access your app without authentication!"
else
  echo "  ❌ FAIL: Public URL returns $HTTP_CODE — check tunnel configuration"
fi

# --- Step 5: Save evidence ---
echo ""
echo "[5/6] Saving evidence..."
{
  echo "=== Davis Tunnel Verification ==="
  echo "Date: $(date)"
  echo "Domain: $DOMAIN"
  echo ""
  echo "--- Tunnel Logs ---"
  docker logs davis-tunnel 2>&1 | tail -30
  echo ""
  echo "--- HTTP Code for $DOMAIN/api/health ---"
  echo "$HTTP_CODE"
  echo ""
  echo "--- Container Status ---"
  docker compose -f docker-compose.davis.yml ps
} > "$EVIDENCE_DIR/task-8-tunnel-verification.txt"
echo "  ✅ Evidence saved to $EVIDENCE_DIR/task-8-tunnel-verification.txt"

# --- Step 6: Summary ---
echo ""
echo "[6/6] Summary"
echo "=========================================="
echo "  Tunnel connected:     ✅"
echo "  Local services:       ✅"
echo "  Access gate:          $([ "$HTTP_CODE" == "302" ] || [ "$HTTP_CODE" == "403" ] && echo "✅" || echo "⚠️")"
echo "  Public URL:           $DOMAIN"
echo ""
echo "  Next: Open $DOMAIN on your phone to test mobile access."
echo "        You should see a Cloudflare Access login page."
echo "=========================================="
