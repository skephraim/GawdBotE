#!/usr/bin/env bash
# GawdBotE — YouTube Agent Builder setup
#
# Clones, builds, and registers the two MCP servers described in the
# "Claude Code Agents from YouTube" guide:
#   1. claude-code-youtube-mcp — transcripts, chapters, comments, metadata
#      (5 of 15 tools work with no key; the rest need a YouTube Data API v3 key)
#   2. yt-analysis-mcp         — hands a YouTube URL straight to Gemini so it
#      can watch the frames/audio (needs a free Gemini API key)
#
# This script only touches YOUR local machine's Claude Code config
# (`claude mcp add -s user ...`). It does not change anything inside the
# GawdBotE repo or its Python runtime — those are separate from Claude Code.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MCP_DIR="$REPO/mcp-servers"
mkdir -p "$MCP_DIR"

echo "╔════════════════════════════════════════════╗"
echo "║   YouTube Agent Builder — MCP setup         ║"
echo "╚════════════════════════════════════════════╝"
echo ""

# ── Preflight ──────────────────────────────────────────────────────────────────
command -v git >/dev/null || { echo "git is required."; exit 1; }
command -v node >/dev/null || { echo "Node.js 18+ is required."; exit 1; }
HAVE_CLAUDE=0
command -v claude >/dev/null && HAVE_CLAUDE=1
HAVE_PNPM=0
command -v pnpm >/dev/null && HAVE_PNPM=1

# ── 1. Transcript layer: claude-code-youtube-mcp ───────────────────────────────
YT_DIR="$MCP_DIR/claude-code-youtube-mcp"
echo "→ Layer 1 — transcript MCP (claude-code-youtube-mcp)"
if [ ! -d "$YT_DIR" ]; then
  git clone https://github.com/wynandw87/claude-code-youtube-mcp.git "$YT_DIR"
else
  echo "  Already cloned, pulling latest..."
  git -C "$YT_DIR" pull --ff-only
fi
( cd "$YT_DIR" && npm install )   # install + build happen together for this repo
YT_ENTRY="$YT_DIR/dist/index.js"
echo "  ✓ Built: $YT_ENTRY"

read -rp "  YouTube Data API v3 key (optional — blank = only the 5 no-key tools work): " YOUTUBE_API_KEY || true
echo ""

# ── 2. Vision layer: yt-analysis-mcp ────────────────────────────────────────────
GEMINI_DIR="$MCP_DIR/yt-analysis-mcp"
echo "→ Layer 2 — visual analysis MCP (yt-analysis-mcp, needs Gemini)"
if [ ! -d "$GEMINI_DIR" ]; then
  git clone https://github.com/Legorobotdude/yt-analysis-mcp.git "$GEMINI_DIR"
else
  echo "  Already cloned, pulling latest..."
  git -C "$GEMINI_DIR" pull --ff-only
fi
if [ "$HAVE_PNPM" -eq 0 ]; then
  echo "  pnpm not found — installing it with 'npm install -g pnpm'..."
  npm install -g pnpm
fi
( cd "$GEMINI_DIR" && pnpm install && pnpm build )
GEMINI_ENTRY="$GEMINI_DIR/dist/index.js"
echo "  ✓ Built: $GEMINI_ENTRY"

GEMINI_API_KEY="${GEMINI_API_KEY:-}"
if [ -z "$GEMINI_API_KEY" ]; then
  read -rp "  Gemini API key (get one free at https://aistudio.google.com/apikey): " GEMINI_API_KEY || true
fi
echo ""

# ── 3. Register with Claude Code ───────────────────────────────────────────────
YT_CMD=(claude mcp add -s user youtube)
[ -n "${YOUTUBE_API_KEY:-}" ] && YT_CMD+=(-e "YOUTUBE_API_KEY=$YOUTUBE_API_KEY")
YT_CMD+=(-- node "$YT_ENTRY")

GEMINI_CMD=(claude mcp add -s user -e "GEMINI_API_KEY=$GEMINI_API_KEY" yt-analysis -- node "$GEMINI_ENTRY")

if [ "$HAVE_CLAUDE" -eq 1 ]; then
  echo "→ Registering both servers with Claude Code..."
  "${YT_CMD[@]}"
  "${GEMINI_CMD[@]}"
  echo "  ✓ Registered. Restart Claude Code, then run: claude mcp list"
else
  echo "  'claude' CLI not found on this machine — run these two commands yourself"
  echo "  wherever your Claude Code CLI is installed:"
  echo ""
  printf '    %q ' "${YT_CMD[@]}"; echo
  echo ""
  printf '    %q ' "${GEMINI_CMD[@]}"; echo
fi

echo ""
echo "╔════════════════════════════════════════════╗"
echo "║  Done. Test it: paste a tutorial URL and    ║"
echo "║  ask Claude Code to watch it.               ║"
echo "╚════════════════════════════════════════════╝"
echo ""
echo "See skills/youtube-agent-builder/SKILL.md for the build-an-agent-from-10-videos workflow."
