#!/usr/bin/env bash
# SuperAI quick-start script
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV="$SCRIPT_DIR/.venv"
PYTHON="${VENV}/bin/python"

# ── Subcommand handling ────────────────────────────────────────────────────────
CMD="${1:-run}"

case "$CMD" in
  install)
    echo "Setting up SuperAI..."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --upgrade pip -q
    "$VENV/bin/pip" install -r requirements.txt
    echo ""
    echo "Done! Copy .env.example to .env and fill in your keys:"
    echo "  cp .env.example .env && nano .env"
    echo "Then run: ./run.sh"
    exit 0
    ;;
  doctor)
    exec "$PYTHON" main.py doctor "${@:2}"
    ;;
  backup)
    exec "$PYTHON" main.py backup "${@:2}"
    ;;
  chat)
    exec "$PYTHON" main.py chat
    ;;
  run|"")
    ;;
  *)
    echo "Usage: ./run.sh [install|run|chat|doctor|backup]"
    exit 1
    ;;
esac

# ── Pre-flight checks ──────────────────────────────────────────────────────────
if [ ! -f "$PYTHON" ]; then
  echo "Virtualenv not found. Run: ./run.sh install"
  exit 1
fi

if [ ! -f "$SCRIPT_DIR/.env" ]; then
  echo "No .env file found. Creating from example..."
  cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
  echo "Edit .env and fill in your API keys, then re-run."
  exit 1
fi

echo "Starting SuperAI..."
exec "$PYTHON" main.py
