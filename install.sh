#!/usr/bin/env bash
# GawdBotE installer — sets up venv, systemd service, and 'gawdbote' CLI command
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO/.venv"
SERVICE_DIR="$HOME/.config/systemd/user"
BIN_DIR="$HOME/.local/bin"

echo "╔══════════════════════════════════════╗"
echo "║        GawdBotE Installer            ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── 1. Python venv + deps ──────────────────────────────────────────────────────
echo "→ Setting up Python virtualenv..."
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -r "$REPO/requirements.txt" -q
echo "  ✓ Dependencies installed"

# ── 2. .env file ───────────────────────────────────────────────────────────────
if [ ! -f "$REPO/.env" ]; then
  cp "$REPO/.env.example" "$REPO/.env"
  echo "  ✓ Created .env from example — edit it with your API keys:"
  echo "    nano $REPO/.env"
else
  echo "  ✓ .env already exists"
fi

# ── 3. systemd user service ────────────────────────────────────────────────────
echo "→ Installing systemd user service..."
mkdir -p "$SERVICE_DIR"
# Substitute %h with actual home path so the service file is concrete
sed "s|%h|$HOME|g" "$REPO/gawdbote.service" > "$SERVICE_DIR/gawdbote.service"
systemctl --user daemon-reload
systemctl --user enable gawdbote.service
echo "  ✓ Service installed and enabled (starts at login)"

# ── 4. 'gawdbote' CLI command ──────────────────────────────────────────────────
echo "→ Installing 'gawdbote' command..."
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/gawdbote" << SCRIPT
#!/usr/bin/env bash
# GawdBotE CLI wrapper
REPO="$REPO"
VENV="$VENV"

CMD="\${1:-help}"
case "\$CMD" in
  start)
    systemctl --user start gawdbote.service
    echo "GawdBotE started."
    ;;
  stop)
    systemctl --user stop gawdbote.service
    echo "GawdBotE stopped."
    ;;
  restart)
    systemctl --user restart gawdbote.service
    echo "GawdBotE restarted."
    ;;
  status)
    systemctl --user status gawdbote.service --no-pager
    ;;
  logs)
    journalctl --user -u gawdbote.service -f --no-pager "\${@:2}"
    ;;
  chat)
    cd "\$REPO" && "\$VENV/bin/python" main.py chat
    ;;
  doctor)
    cd "\$REPO" && "\$VENV/bin/python" main.py doctor "\${@:2}"
    ;;
  backup)
    cd "\$REPO" && "\$VENV/bin/python" main.py backup "\${@:2}"
    ;;
  evolve)
    shift
    MSG="\$*"
    if [ -z "\$MSG" ]; then echo "Usage: gawdbote evolve <request>"; exit 1; fi
    curl -s -X POST http://localhost:8080/webhook/evolve \\
      -H "X-Webhook-Secret: \$(grep WEBHOOK_SECRET "\$REPO/.env" 2>/dev/null | cut -d= -f2 | tr -d ' ')" \\
      -H "Content-Type: application/json" \\
      -d "{\"request\":\"\$MSG\"}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('result','No response'))"
    ;;
  ask)
    shift
    MSG="\$*"
    if [ -z "\$MSG" ]; then echo "Usage: gawdbote ask <message>"; exit 1; fi
    curl -s -X POST http://localhost:8080/webhook \\
      -H "X-Webhook-Secret: \$(grep WEBHOOK_SECRET "\$REPO/.env" 2>/dev/null | cut -d= -f2 | tr -d ' ')" \\
      -H "Content-Type: application/json" \\
      -d "{\"message\":\"\$MSG\"}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('response','No response'))"
    ;;
  help|--help|-h|"")
    echo "GawdBotE — commands:"
    echo "  gawdbote start       Start the background service"
    echo "  gawdbote stop        Stop the background service"
    echo "  gawdbote restart     Restart the background service"
    echo "  gawdbote status      Show service status"
    echo "  gawdbote logs        Follow live logs (Ctrl+C to stop)"
    echo "  gawdbote chat        Interactive CLI chat"
    echo "  gawdbote ask <msg>   Send a one-shot message (service must be running)"
    echo "  gawdbote evolve <r>  Trigger self-improvement (service must be running)"
    echo "  gawdbote doctor      Run health checks"
    echo "  gawdbote backup      Create a backup archive"
    ;;
  *)
    echo "Unknown command: \$CMD  (try: gawdbote help)"
    exit 1
    ;;
esac
SCRIPT
chmod +x "$BIN_DIR/gawdbote"
echo "  ✓ 'gawdbote' command installed to $BIN_DIR/gawdbote"

# ── 5. Ensure ~/.local/bin is in PATH ─────────────────────────────────────────
if ! echo "$PATH" | grep -q "$BIN_DIR"; then
  echo ""
  echo "  ⚠  Add this to your ~/.bashrc or ~/.zshrc:"
  echo '     export PATH="$HOME/.local/bin:$PATH"'
fi

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════╗"
echo "║           Install complete!          ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "Next steps:"
echo "  1. Edit your config:  nano $REPO/.env"
echo "  2. Run health checks: gawdbote doctor"
echo "  3. Start the service: gawdbote start"
echo "  4. Follow the logs:   gawdbote logs"
echo "  5. Chat from CLI:     gawdbote chat"
echo ""
echo "The service will auto-start on every login."
