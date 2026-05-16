#!/usr/bin/env bash
# start.sh — launch the COBOL Parser Pipeline app (API + UI together)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Activate virtual environment ───────────────────────────────────────────────
if [[ -f ".venv/bin/activate" ]]; then
    source .venv/bin/activate
else
    echo "ERROR: Virtual environment not found. Run './run.sh --setup' first."
    exit 1
fi

# ── Ensure DB directory exists ─────────────────────────────────────────────────
mkdir -p artifacts output/diagrams output/java output/specs

# ── Start FastAPI (serves both API and UI at http://localhost:8000) ────────────
echo ""
echo "  ╔══════════════════════════════════════════════════════════╗"
echo "  ║   COBOL Parser Pipeline — UST CodeCrafter Championship   ║"
echo "  ╠══════════════════════════════════════════════════════════╣"
echo "  ║   Dashboard  →  http://localhost:8000                    ║"
echo "  ║   API docs   →  http://localhost:8000/docs               ║"
echo "  ║                                                          ║"
echo "  ║   Press Ctrl+C to stop                                   ║"
echo "  ╚══════════════════════════════════════════════════════════╝"
echo ""

# Open browser after a short delay (macOS)
if command -v open &>/dev/null; then
    (sleep 2 && open "http://localhost:8000") &
fi

# Start server (foreground — Ctrl+C stops it)
exec uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
