#!/usr/bin/env bash
# gemini_sync.sh
# Waits for Chrome CDP to be available, then crawls Gemini conversations.
#
# Prerequisites:
#   1. On Windows: double-click tools/windows/launch_chrome_debug.bat
#   2. Log in to gemini.google.com in the Chrome window that opens
#   3. In WSL2: bash ~/ai-archive/tools/gemini_sync.sh
#
# The script ONLY crawls (does not run normalize/cluster/curate).
# Run the full pipeline manually afterwards if desired:
#   cd ~/ai-archive && uv run ai-archive run --provider gemini

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CDP_URL="http://127.0.0.1:9222"
MAX_WAIT=60   # seconds to wait for Chrome CDP

cd "$PROJECT_DIR"

# ── 1. Wait for Chrome CDP ───────────────────────────────────────────────────
echo "Waiting for Chrome remote debugging on $CDP_URL ..."
elapsed=0
until curl -sf "$CDP_URL/json/version" > /dev/null 2>&1; do
    if (( elapsed >= MAX_WAIT )); then
        echo ""
        echo "ERROR: Chrome not reachable at $CDP_URL after ${MAX_WAIT}s."
        echo "  → On Windows, double-click: tools/windows/launch_chrome_debug.bat"
        echo "  → Make sure the firewall allows port 9222."
        exit 1
    fi
    printf "."
    sleep 2
    (( elapsed += 2 ))
done
echo ""
echo "Chrome CDP ready ($(curl -sf "$CDP_URL/json/version" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("Browser","?"))' 2>/dev/null || echo 'connected'))"

# ── 2. Enable Gemini in env if currently disabled ───────────────────────────
if grep -q "^GEMINI_ENABLED=false" .env 2>/dev/null; then
    echo ""
    echo "NOTE: GEMINI_ENABLED=false in .env — temporarily enabling for this run."
    # Use sed to flip it in a subshell env export (does not modify .env permanently)
    export GEMINI_ENABLED=true
fi

# ── 3. Crawl Gemini only ─────────────────────────────────────────────────────
echo ""
echo "Starting Gemini crawl..."
echo "  Project: $PROJECT_DIR"
echo "  CDP:     $CDP_URL"
echo ""

uv run ai-archive crawl --provider gemini

echo ""
echo "Crawl complete."
echo ""
echo "Next steps (run manually when ready):"
echo "  uv run ai-archive normalize --provider gemini"
echo "  uv run ai-archive cluster"
echo "  uv run ai-archive curate"
echo "  uv run ai-archive export"
