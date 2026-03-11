# SuperAI Architecture

SuperAI is a self-improving AI assistant combining the best of GawdBot and OpenClaw.
This file is read by the agent before any self-evolution task.

## Directory Layout

```
superai/
├── core/
│   ├── agent.py        # Agentic loop — dispatches tool calls, calls LLM
│   ├── llm.py          # Multi-provider LLM with automatic fallback
│   ├── memory.py       # SQLite persistent memory + cosine similarity search
│   ├── voice.py        # Wake word, STT (faster-whisper), TTS (piper)
│   └── self_evolve.py  # Self-improvement cycle (reads this file first)
├── tools/
│   ├── code_tools.py   # File read/write, shell commands
│   ├── git_tools.py    # Git operations + GitHub PR creation
│   ├── pc_control.py   # Mouse, keyboard, clipboard, screenshots, windows
│   ├── vision.py       # Vision LLM — analyze screenshots
│   └── web_search.py   # DuckDuckGo / Brave web search
├── interfaces/
│   ├── telegram_bot.py # Telegram (commands: /evolve, /memory, /search)
│   ├── discord_bot.py  # Discord (!evolve, !memory, !search, @mentions)
│   ├── slack_bot.py    # Slack Socket Mode (mention or DM)
│   └── webhook_server.py # HTTP API (/webhook, /webhook/embed, /webhook/evolve)
├── scheduler/
│   └── cron.py         # Cron job scheduler (CRON_JOBS env var)
├── cli/
│   ├── doctor.py       # Health checks: python main.py doctor
│   └── backup.py       # Backup/restore: python main.py backup create
├── data/               # SQLite DB and runtime state (gitignored)
├── backups/            # Archives from `python main.py backup create`
├── config.py           # All settings from .env
├── main.py             # Entry point — starts all interfaces concurrently
└── requirements.txt
```

## Key Design Decisions

- **Python 3.11+** — async throughout (`asyncio.gather` for parallel tool calls)
- **Multi-provider LLM fallback** — providers in `LLM_PROVIDERS` tried in order
- **No external vector DB** — SQLite + cosine similarity for memory
- **OpenAI-compat API** — NVIDIA NIM, Ollama, OpenAI, OpenRouter all share the same client
- **All interfaces run concurrently** via `asyncio.gather` in `main.py`
- **Tool calls run in parallel** within the agent loop

## Adding a New Tool

1. Implement the function in `tools/` (keep it pure/async-friendly)
2. Add a tool spec to `TOOLS` list in `core/agent.py`
3. Add dispatch logic in the `_dispatch()` function in `core/agent.py`

## Adding a New Interface

1. Create `interfaces/myplatform.py` with an `async def run()` coroutine
2. Import and add a task in `main.py`
3. Add config vars to `config.py` and `.env.example`

## Self-Evolution Rules

- Always read this file first
- Change the minimum needed
- Run `python -m py_compile <file>` before committing
- Commit to `evolve-YYYYMMDD-HHMMSS` branch
- Create a PR — never auto-merge unless SELF_EVOLVE_AUTO_MERGE=true
- Never modify .env, .git, or data/
