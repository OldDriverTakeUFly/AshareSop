#!/usr/bin/env bash
# ── Invest SOP Installation Script ───────────────────────────────────
# Sets up DB migration, logs directory, and cron entries.
# Usage:
#   bash install.sh           # full install
#   bash install.sh --dry-run # preview actions without executing
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CRONTAB_FILE="$SCRIPT_DIR/crontab.txt"
LOGS_DIR="$SCRIPT_DIR/logs"
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
MARKER="# INVEST_SOP_CRON_START"
MARKER_END="# INVEST_SOP_CRON_END"

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
    echo "=== DRY RUN MODE — no changes will be made ==="
    echo ""
fi

run_cmd() {
    if $DRY_RUN; then
        echo "[DRY-RUN] $*"
    else
        echo "[EXEC]   $*"
        eval "$@"
    fi
}

echo "── Invest SOP Installer ──────────────────────────"
echo "Project root: $PROJECT_ROOT"
echo ""

# ── Step 1: Verify prerequisites ─────────────────────────────────────
echo "1. Checking prerequisites..."

if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "   ERROR: Python venv not found at $VENV_PYTHON"
    echo "   Create it first: python -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi
echo "   ✓ Python venv found: $VENV_PYTHON"

if [[ ! -f "$CRONTAB_FILE" ]]; then
    echo "   ERROR: crontab.txt not found at $CRONTAB_FILE"
    exit 1
fi
echo "   ✓ crontab.txt found"

# ── Step 2: Run database migration ───────────────────────────────────
echo ""
echo "2. Running database migration..."
run_cmd "cd $PROJECT_ROOT && PYTHONPATH=$PROJECT_ROOT $VENV_PYTHON -m stockhot.storage.database"
echo "   ✓ Database migration complete"

# ── Step 3: Create logs directory ────────────────────────────────────
echo ""
echo "3. Setting up logs directory..."
run_cmd "mkdir -p $LOGS_DIR"
echo "   ✓ Logs directory ready: $LOGS_DIR"

# ── Step 4: Install cron entries (append, do NOT replace) ────────────
echo ""
echo "4. Installing cron entries..."

# Check if already installed by looking for our marker
EXISTING=$(crontab -l 2>/dev/null || true)
if echo "$EXISTING" | grep -qF "$MARKER"; then
    echo "   ⚠ Invest SOP cron entries already installed (marker found). Skipping."
    echo "   To reinstall, remove the block between $MARKER and $MARKER_END from your crontab first."
else
    # Build the new block
    CRON_BLOCK=""
    CRON_BLOCK="${CRON_BLOCK}${MARKER}\n"
    # Read crontab.txt, skip comment and blank lines — keep only active cron lines
    while IFS= read -r line; do
        # Skip empty lines and comments
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        CRON_BLOCK="${CRON_BLOCK}${line}\n"
    done < "$CRONTAB_FILE"
    CRON_BLOCK="${CRON_BLOCK}${MARKER_END}\n"

    if $DRY_RUN; then
        echo "[DRY-RUN] Would append the following to crontab:"
        echo ""
        echo -e "$CRON_BLOCK" | sed 's/^/         /'
    else
        # Append to existing crontab
        (echo "$EXISTING"; echo -e "$CRON_BLOCK") | crontab -
        echo "   ✓ Cron entries installed successfully"
    fi
fi

# ── Step 5: Verify ───────────────────────────────────────────────────
echo ""
echo "5. Verifying installation..."

if $DRY_RUN; then
    echo "   [DRY-RUN] Would verify: crontab contains $MARKER"
    echo "   [DRY-RUN] Would verify: $LOGS_DIR exists"
    echo "   [DRY-RUN] Would verify: $VENV_PYTHON is executable"
else
    # Verify crontab
    INSTALLED=$(crontab -l 2>/dev/null || true)
    if echo "$INSTALLED" | grep -qF "$MARKER"; then
        echo "   ✓ Cron entries present in crontab"
    else
        echo "   ✗ FAILED: Cron entries not found in crontab"
        exit 1
    fi

    # Verify logs dir
    if [[ -d "$LOGS_DIR" ]]; then
        echo "   ✓ Logs directory exists"
    else
        echo "   ✗ FAILED: Logs directory not found"
        exit 1
    fi

    # Count installed entries
    ENTRY_COUNT=$(echo "$INSTALLED" | grep -cF "invest_sop/scripts/" || true)
    echo "   ✓ Found $ENTRY_COUNT cron entries for invest_sop"
fi

echo ""
echo "═══════════════════════════════════════════════════"
if $DRY_RUN; then
    echo "  DRY RUN complete — no changes were made."
else
    echo "  ✓ Invest SOP installation complete!"
fi
echo "═══════════════════════════════════════════════════"
echo ""
echo "  Logs:    $LOGS_DIR/"
echo "  Crontab: crontab -l"
echo "  Remove:  crontab -e (delete block between markers)"
