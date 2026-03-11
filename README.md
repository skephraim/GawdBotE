# GawdBotE

A self-improving AI assistant combining the best of GawdBot and OpenClaw.

```
Voice (wake word) ─┐
Telegram           │
Discord            ├──► GawdBotE Agent ──► Memory (SQLite + embeddings)
Slack              │         │
Webhooks (HTTP)    ┘         ├──► Files / Git / GitHub PRs
                             ├──► PC control (mouse, keyboard, windows)
                             ├──► Web search (DuckDuckGo / Brave)
                             ├──► Skills (52 built-in, install more via clawhub)
                             └──► Self-improvement (reads + edits own code)
```

## Features

| Feature | How |
|---|---|
| Voice wake word | openwakeword ("hey jarvis") |
| Speech-to-text | faster-whisper (local GPU/CPU) |
| Text-to-speech | Piper TTS (local, offline) |
| Chat | Telegram + Discord + Slack + CLI |
| LLM backend | Multi-provider with fallback: NVIDIA NIM → OpenRouter → Ollama |
| Text embeddings | NVIDIA NIM, Ollama, or OpenAI |
| Persistent memory | SQLite + cosine similarity search |
| Web search | DuckDuckGo (free) or Brave Search API |
| Skills | 52 built-in; install more via `clawhub install <skill>` |
| Coding agent | Read/write files, run commands, git commit, create GitHub PRs |
| PC control | Mouse, keyboard, hotkeys, windows, clipboard, screenshots |
| Webhooks | POST `/webhook` to trigger agent |
| Self-evolution | `/evolve <request>` → agent edits own code → git branch → PR |
| Cron jobs | Scheduled agent tasks via `CRON_JOBS` config |
| CLI tools | `doctor` health checks, `backup create/verify` |

## Quick Start

See **[QUICKSTART.md](QUICKSTART.md)** for the full step-by-step installation guide.

```bash
# 1. Clone and install
git clone https://github.com/skephraim/GawdBotE
cd GawdBotE
./install.sh

# 2. Configure
cp .env.example .env
nano .env   # fill in your API keys

# 3. Health check
gawdbote doctor

# 4. Run
gawdbote chat     # interactive CLI session
gawdbote start    # start as a background service
gawdbote logs     # watch live output
```

## LLM Providers (Auto-Fallback)

Set `LLM_PROVIDERS` in `.env` — providers are tried in order:

| Provider | Config | Notes |
|---|---|---|
| NVIDIA NIM | `NVIDIA_API_KEY` | Free tier at build.nvidia.com |
| OpenRouter | `OPENROUTER_API_KEY` | **Default backup** — 200+ models |
| Ollama | `OLLAMA_BASE_URL` | Fully local, no API key |
| OpenAI | `OPENAI_API_KEY` | GPT-4o etc. |
| Anthropic | `ANTHROPIC_API_KEY` | Claude models |

## Skills

52 skills are built in. They auto-inject relevant instructions into the agent based on your request:

```bash
# List installed skills
./run.sh chat
> skill_list

# Install more from clawhub.com
npm i -g clawhub
clawhub install postgres
clawhub update --all
```

Built-in skills include: GitHub, Notion, Obsidian, Slack, Discord, Trello, tmux, weather, Spotify, Telegram, web summarizer, coding agent, image generation, Whisper transcription, and more.

## Interfaces

### Telegram
```bash
TELEGRAM_BOT_TOKEN=...
TELEGRAM_USER_ID=...
```
Commands: `/evolve`, `/memory`, `/search`, voice messages

### Discord
```bash
DISCORD_ENABLED=true
DISCORD_BOT_TOKEN=...
```
Commands: `!evolve`, `!memory`, `!search`, @mentions

### Slack
```bash
SLACK_ENABLED=true
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
```
Mention the bot or DM it.

### Webhooks
```bash
curl -X POST http://localhost:8080/webhook \
  -H "X-Webhook-Secret: your-secret" \
  -H "Content-Type: application/json" \
  -d '{"message": "what files are in the project?"}'
```

Endpoints: `/webhook`, `/webhook/embed`, `/webhook/evolve`, `/webhook/search`

## Self-Evolution

```bash
# Via Telegram
/evolve add a Notion sync tool

# Via Discord
!evolve add weather alerts every morning

# Via voice
"Hey Jarvis, improve yourself — add rate limiting to the webhook server"
```

The agent reads `CLAUDE.md` for architecture context, edits source files, runs syntax checks, commits to a branch, and creates a GitHub PR.

## Cron Jobs

```bash
CRON_JOBS=[
  {"schedule":"0 9 * * *","message":"Good morning! Summarize the latest GitHub issues."},
  {"schedule":"0 18 * * 5","message":"It's Friday — create a weekly summary."}
]
```
