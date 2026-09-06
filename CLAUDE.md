# GawdBotE Architecture

GawdBotE is a self-improving AI assistant combining the best of GawdBot and OpenClaw.
This file is read by the agent before any self-evolution task.

## Directory Layout

```
gawdbote/
├── core/
│   ├── agent.py        # Agentic loop — dispatches tool calls, calls LLM
│   ├── llm.py          # Multi-provider LLM with automatic fallback
│   ├── memory.py       # SQLite persistent memory + cosine similarity search
│   ├── voice.py        # Wake word, STT (faster-whisper), TTS (piper)
│   ├── self_evolve.py  # Self-improvement cycle (reads this file first)
│   └── leads.py        # Keisha's SQLite storage: leads, outbox, suppression
├── tools/
│   ├── code_tools.py   # File read/write, shell commands
│   ├── git_tools.py    # Git operations + GitHub PR creation
│   ├── pc_control.py   # Mouse, keyboard, clipboard, screenshots, windows
│   ├── vision.py       # Vision LLM — analyze screenshots
│   ├── web_search.py   # DuckDuckGo / Brave web search
│   └── keisha.py       # Lead scoring, pitch drafting, approval queue, sending
├── interfaces/
│   ├── telegram_bot.py # Telegram (commands: /evolve, /memory, /search)
│   ├── discord_bot.py  # Discord (!evolve, !memory, !search, @mentions)
│   ├── slack_bot.py    # Slack Socket Mode (mention or DM)
│   └── webhook_server.py # HTTP API (/webhook, /webhook/embed, /webhook/evolve)
├── scheduler/
│   └── cron.py         # Cron job scheduler (CRON_JOBS env var)
├── cli/
│   ├── doctor.py       # Health checks: python main.py doctor
│   ├── backup.py       # Backup/restore: python main.py backup create
│   └── keisha.py       # Lead pipeline: python main.py keisha review|approve|send
├── data/               # SQLite DBs (memory.db, leads.db) and runtime state (gitignored)
├── docs/
│   └── KEISHA.md       # Keisha guide + Python/SQL/CCL walkthrough
├── examples/           # Sample CSVs for `keisha import`
├── backups/            # Archives from `python main.py backup create`
├── config.py           # All settings from .env
├── main.py             # Entry point — starts all interfaces concurrently
├── requirements.txt
├── install.sh          # One-shot installer: venv + systemd service + gawdbote CLI
├── gawdbote.service    # systemd unit template (install.sh copies to ~/.config/systemd/user/)
└── Makefile            # make setup/start/stop/restart/status/logs/chat/doctor/backup
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

## Keisha Rules (lead-gen agent)

- Qualified leads carry a tier: A = missed-call evidence (work first),
  B = target niche + no website. `find_leads` orders A before B; do not
  change that ordering without a reason.
- The `missed_calls` column is sticky (`MAX(old, new)`): a sparse re-import
  must never unlearn a known missed-call problem.
- New columns need an entry in `_MIGRATIONS` in core/leads.py. Order in
  `_conn()` is tables → migrate → indexes, never indexes before migrate.
- Keisha NEVER sends outreach autonomously. Drafts go to the `outbox` table as
  `pending` and require a human to set them `approved`.
- `keisha_send` / `keisha send` delivers ONLY `approved` rows, re-checks the
  suppression list at send time, and stops at `KEISHA_DAILY_LIMIT`.
- The daily counter comes from the database, never an in-memory variable —
  a restart must not reset it.
- Every outbound email carries the CAN-SPAM footer (sender, postal address,
  opt-out). Honor every STOP immediately via `keisha_block`.
- Do not add automated DM sending for Instagram / X / LinkedIn: it violates
  their Terms of Service.

## Self-Evolution Rules

- Always read this file first
- Change the minimum needed
- Run `python -m py_compile <file>` before committing
- Commit to `evolve-YYYYMMDD-HHMMSS` branch
- Create a PR — never auto-merge unless SELF_EVOLVE_AUTO_MERGE=true
- Never modify .env, .git, or data/
