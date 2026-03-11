# Lesson 13 — Architecture Review

## The Full Picture

Let's zoom out and see how every piece connects.

```
┌─────────────────────────────────────────────────────────────────┐
│                        INTERFACES                               │
│   Telegram  Discord  Slack  Webhooks  Voice  CLI  Cron          │
└──────────────────────────┬──────────────────────────────────────┘
                           │ agent.run(message, source)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    CORE AGENT LOOP                              │
│                                                                 │
│  1. Inject relevant skills into system prompt                   │
│  2. Call LLM (with fallback chain)                              │
│  3. If tool calls → dispatch in parallel → add results          │
│  4. If final answer → store in memory → return                  │
└──────┬────────────────────────────────────┬──────────────────────┘
       │                                    │
       ▼                                    ▼
┌─────────────┐                    ┌──────────────────┐
│  LLM LAYER  │                    │   TOOLS          │
│             │                    │                  │
│  NVIDIA NIM │                    │  read_file       │
│     ↓       │                    │  write_file      │
│  OpenRouter │                    │  run_command     │
│     ↓       │                    │  web_search      │
│  Ollama     │                    │  git_commit      │
│  (local)    │                    │  mouse_click     │
└─────────────┘                    │  take_screenshot │
                                   │  memory_store    │
                                   │  memory_search   │
                                   │  skill_install   │
                                   └──────────────────┘
       │                                    │
       ▼                                    ▼
┌─────────────┐                    ┌──────────────────┐
│   SKILLS    │                    │    MEMORY        │
│             │                    │                  │
│ 52 SKILL.md │                    │  SQLite DB       │
│ files       │                    │  Embeddings      │
│ auto-inject │                    │  Cosine search   │
│ by keyword  │                    └──────────────────┘
└─────────────┘
```

---

## Every Design Decision, Explained

### Why Python?
Python has the best ecosystem for AI/ML work. The libraries we needed — `openai`, `faster-whisper`, `pyautogui`, `python-telegram-bot`, `discord.py`, `slack-bolt` — all have mature Python packages. Python's `asyncio` handles concurrent interfaces cleanly.

### Why asyncio instead of threads?
GawdBotE handles many things at once: Telegram messages, Discord messages, cron jobs, the webhook server. Threads would work but require locks and are prone to race conditions. `asyncio` uses cooperative multitasking — each coroutine yields control when it's waiting (for network, disk, or sleep). Single-threaded but concurrent.

### Why SQLite for memory instead of a vector database?
Vector databases (Pinecone, Weaviate, Chroma) are powerful but heavy. SQLite is built into Python, requires no server, and is just a file. For a personal assistant with thousands (not millions) of memories, loading all vectors and computing cosine similarity in pure Python is fast enough — typically under 100ms even with 10,000 memories.

### Why OpenAI-compatible API for all providers?
OpenAI set the industry standard. NVIDIA NIM, Ollama, OpenRouter, and many others all implement the same API. By building against this standard, we can swap providers with zero code changes — just update the URL and key.

### Why SKILL.md files instead of hardcoding instructions?
Skills are **data, not code**. They can be added, removed, and updated without changing any Python. Anyone can write a skill. The community can share them via clawhub.com. This makes the system extensible in a way that's accessible to non-programmers.

### Why pull requests for self-evolution?
Self-evolution is powerful but not infallible. The agent might make a logically correct but stylistically wrong change. PR review lets you catch issues before they go live while still automating the coding work. Think of it as having a junior developer who codes but waits for your approval.

### Why systemd instead of a Docker container?
For a personal assistant that needs access to your desktop (screenshots, mouse control, clipboard), containerization adds complexity without benefit. systemd is built into every Linux system, is trivial to configure, and gives you auto-start + auto-restart + log collection for free.

---

## The Data Flow for One Request

Request: *"Hey Jarvis, what's the latest news about Python?"*

1. **Voice** — wake word detected → record audio → Whisper transcribes → "what's the latest news about Python"

2. **Interfaces** → `agent.run("what's the latest news about Python", source="voice")`

3. **Skills** — "python" and "news" match skills? Loosely. No strong skill match, so no injection.

4. **LLM call #1** — sends message + tools list to NVIDIA NIM
   - LLM thinks: "I need current information → I should search the web"
   - Returns: `tool_call: web_search(query="Python programming language news 2026")`

5. **Tool dispatch** — runs `web_search.search("Python programming language news 2026")`
   - Makes async HTTP request to DuckDuckGo
   - Returns: "Python 3.14 released... PEP 750 approved..."

6. **LLM call #2** — sends [original message + tool result] to NVIDIA NIM
   - LLM thinks: "I have the search results, I can answer now"
   - Returns: "The latest Python news: Python 3.14 was just released with..."

7. **Memory** — stores "User asked about Python news, told them about 3.14 release"

8. **Voice** — Piper TTS converts response to audio → speakers

Total time: ~3-5 seconds (most of it is the two LLM API calls)

---

## What Could Be Better

No project is perfect. Things you could improve:

| Limitation | Potential Fix |
|------------|--------------|
| No rate limiting | Add per-user rate limiting in each interface |
| Memory search loads all rows | Add approximate nearest-neighbor indexing for scale |
| No conversation threading | Track separate conversation histories per user/channel |
| Skills keyword matching is basic | Use embedding similarity for better relevance |
| Single `main.py` process | Separate services for resilience at scale |
| No auth for Discord | Add allowlist of authorized users |
| Cron results go nowhere | Wire cron results to a notification interface |

These are great self-evolution requests.

---

## Extending GawdBotE — The Three Patterns

**1. New Tool** (add a new action the agent can take)
- Write function in `tools/`
- Add JSON definition to `TOOLS` in `core/agent.py`
- Add dispatch in `_dispatch()`

**2. New Interface** (add a new way to reach the agent)
- Create `interfaces/myplatform.py` with `async def run():`
- Call `await agent.run(message, source="myplatform")`
- Add `asyncio.create_task(myplatform.run())` in `main.py`

**3. New Skill** (give the agent knowledge about a specific tool/API)
- Create `skills/my-skill/SKILL.md`
- Write clear instructions/examples in the body
- The agent will auto-use it when relevant keywords appear

---

## The Bigger Picture

GawdBotE demonstrates three ideas that are shaping modern software:

**1. LLMs as reasoning engines, not just text generators**
The LLM isn't generating a response — it's deciding what to do, executing it, observing the result, and deciding what to do next. This turns a language model into a general-purpose reasoning system.

**2. Prompt engineering as software architecture**
`CLAUDE.md`, `SYSTEM_PROMPT`, and skills are software artifacts. They determine behavior as much as the code does. Writing good prompts is as important as writing good functions.

**3. Self-modifying systems**
Software that reads and edits its own source code is no longer science fiction. GawdBotE does it today, carefully, with human review. As LLMs improve, the "carefully" part becomes less necessary and the "human review" part becomes optional.

---

## Final Exercise — Build Something New

Pick one of these and implement it:

1. **SMS interface** — use Twilio to let GawdBotE receive and respond to text messages
2. **Email interface** — use the `himalaya` skill and an email library to check and respond to emails
3. **Brightness control tool** — add a tool that adjusts screen brightness via `xrandr`
4. **Daily digest** — add a cron job that collects GitHub issues + weather + calendar and sends a morning briefing to Telegram

Each of these follows one of the three extension patterns above. Start by reading the relevant lesson, then read the code it references, then make your change.

---

## Congratulations

You now understand:

- How AI agents work (agentic loop, tool calling)
- How to talk to LLMs via API (messages, roles, tokens)
- Multi-provider fallback for resilience
- Building tools (files, shell, PC control)
- Web search (async HTTP, DuckDuckGo/Brave)
- Semantic memory (embeddings, cosine similarity, SQLite)
- Skills (dynamic prompt injection, clawhub)
- Multiple interfaces (Telegram, Discord, Slack, webhooks)
- Voice (wake word, Whisper STT, Piper TTS)
- Self-evolution (agent editing its own code, git PRs)
- Scheduling and deployment (cron, systemd)

That's a complete modern AI application stack — from the model API all the way to a persistent background service with a CLI. Most of what you see in commercial AI products is a version of these same ideas.

Now go build something with it.
