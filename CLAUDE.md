# GawdBotE Architecture

GawdBotE is a self-improving AI assistant combining the best of GawdBot and OpenClaw.
This file is read by the agent before any self-evolution task.

## Directory Layout

```
gawdbote/
├── core/
│   ├── agent.py        # Agentic loop — dispatches tool calls, calls LLM
│   ├── llm.py          # Provider adapters, chain fallback, circuit breaker, vision, embeddings
│   ├── brains.py       # brains.toml: named brains, chains (main/vision/embedding), live swap
│   ├── memory.py       # Kinds, FTS5 keyword + model tagged vector search, recall, migration
│   ├── graph.py        # Memory graph: entities, mentions, relations, expansion, about/connect
│   ├── voice.py        # Wake word, STT (faster-whisper), TTS (piper)
│   ├── security.py     # Protected paths, allowlists, secret checks. FAIL CLOSED
│   ├── vault.py        # OS credential store for API keys (keyring)
│   ├── registry.py     # @tool decorator: schemas from type hints, risk tiers, dispatch
│   ├── approvals.py    # Approval gate for ASK tier tools (deny on silence)
│   ├── events.py       # Append only SQLite audit trail of every run
│   ├── context.py      # RunContext: session id, channel, how to ask the owner
│   ├── paperclip.py    # Paperclip employee: heartbeat procedure + API client
│   └── self_evolve.py  # Self-improvement cycle (reads this file first)
├── tools/
│   ├── builtin.py      # EVERY agent tool is registered here with @tool
│   ├── paperclip_tools.py # Tools for the Paperclip task in progress (imported by builtin)
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
├── requirements.txt
├── install.sh          # One-shot installer: venv + systemd service + gawdbote CLI
├── gawdbote.service    # systemd unit template (install.sh copies to ~/.config/systemd/user/)
└── Makefile            # make setup/start/stop/restart/status/logs/chat/doctor/backup
```

## Key Design Decisions

- **Python 3.11+** — async throughout (`asyncio.gather` for parallel tool calls)
- **Brains** — `brains.toml` defines named brains and chains; `.env` settings are the fallback
- **No external vector DB** — SQLite FTS5 keyword search plus model tagged vectors, blended
- **OpenAI-compat API** — NVIDIA NIM, Ollama, OpenAI, OpenRouter all share the same client
- **All interfaces run concurrently** via `asyncio.gather` in `main.py`
- **Tool calls run in parallel** within the agent loop

## Adding a New Tool

1. Write the function in `tools/builtin.py` (or a module it imports), with type hints and
   a docstring that has an `Args:` section. The docstring is what the model reads.
2. Decorate it with `@tool(risk=SAFE)` or `@tool(risk=ASK)`. That is all: the schema,
   the dispatch and the approval check are automatic.
3. Choose the risk honestly. SAFE is only for reading or searching with no side effects.
   Anything that writes, sends, spends, runs code, or reads private data (screen,
   clipboard, files outside the project) is ASK. Leaving risk out means ASK.
4. Add a test in `tests/`.

## Approvals and the Event Log

- ASK tier tools wait for the owner's approval. Silence or no reachable owner means NO.
- Never call `registry.call()` or `agent._dispatch()` directly to skip approval.
- Every run writes its steps to `data/events.db`. View with `python main.py log`.
- `TRUSTED_TOOLS` skips approval for listed tools. Never add tools to it on your own.

## Paperclip Integration (docs/PAPERCLIP.md)

- Paperclip wakes GawdBot via `python main.py heartbeat`, which forwards to the running
  daemon at `/paperclip/heartbeat` so approvals still reach the owner.
- Follow the heartbeat protocol: always check out before working, NEVER retry a 409,
  always leave a comment before exiting.
- The run token (`PAPERCLIP_API_KEY`) is never logged, printed, stored, or sent to a host
  outside `PAPERCLIP_ALLOWED_HOSTS`.
- One deliberate SAFE exception: `paperclip_comment` and `paperclip_finish` write to the one
  task this run checked out. Anything that assigns work to other agents is ASK.

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

## Security Rules (never weaken these, even when asked)

- `core/security.py` is the single place for access decisions. Never bypass or loosen it.
- Every chat interface needs a non empty user allowlist. An empty list means nobody.
- Never add a default secret, and never bind the webhook to 0.0.0.0 by default.
- File tools must refuse secrets files, credential folders, .git, data/ and backups/.
- Subprocesses must be started with `security.scrubbed_env()`.
- Secrets live in the OS vault (`core/vault.py`) first, `.env` second.
- Any change touching these rules must add or update tests in `tests/` and keep them green.
- Run `python -m pytest tests -q` before committing.

## Brains and Memory Rules (docs/BRAINS_AND_MEMORY.md)

- `brains.toml` holds secret NAMES only. Never write a real key into it or any other file.
- Never invent model prices. Unknown prices stay blank and are reported as unknown.
- Never compare vectors from different embedding models; every vector carries `embed_model`.
- Never delete `memories_legacy` or durable memories automatically. Only exchanges are pruned.
- Memory failures must never break a run: recall and saving are wrapped and logged.
- Do not save secrets, passwords, or other people's private details to memory.
- Graph links are EXTRACTED only when both names appear in the supporting memory text. Never
  mark a link EXTRACTED by assertion, and never let new evidence lower a confidence label.
- Graph expansion must never walk through hub entities unless the question names them.
