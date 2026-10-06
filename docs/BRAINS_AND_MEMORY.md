# Brains and memory

## Brains: which AI models GawdBot thinks with

A **brain** is one model at one provider. A **chain** is the order brains are tried for a job:

| Chain | Job | Notes |
|---|---|---|
| `main` | The agent's thinking | Must support tool calling |
| `vision` | Screenshots and images | `look_at_screen` uses it |
| `embedding` | Vectors for memory search | Optional: keyword search works without it |

If a brain fails (down, out of credit, no key), the next one answers and the reason shows up
in `/trace`. A brain that just failed is skipped for 60 seconds so a dead server doesn't slow
down every message, like downtime procedures for a system that's confirmed down.

### Set it up

1. `copy brains.example.toml brains.toml` and edit it. Without `brains.toml`, the old `.env`
   settings (`LLM_PROVIDERS` and friends) keep working exactly as before.
2. Store each key once: `python main.py secrets set ANTHROPIC_API_KEY`. `brains.toml` holds
   only the **name** of the secret. Pasting a real key there is refused.
3. Fill in `price_in` and `price_out` (USD per million tokens) from each provider's pricing
   page if you want dollar amounts. Blank means "cost unknown", never a guess.
4. Check it: `python main.py brain`, then `python main.py brain test`.

### Swap brains live

| Where | Command |
|---|---|
| Telegram | `/brain`, `/brain use claude-max`, `/brain reset` |
| Discord | `!brain`, `!brain use local`, `!brain reset` |
| CLI chat | `/brain`, `/brain use claude-fast` |
| Terminal | `python main.py brain use local` |

The choice survives restarts. Editing `brains.toml` takes effect on the next message.

### Free brains and your privacy

Mark any brain whose provider may train on your prompts or let people read them with
`shares_data = true` (most free tiers, including the FreeLLMAPI router). That brain never
receives your saved memories and is never used to embed them, even when the chain falls back
to it. Your message and this run's tool results still go to it, so keep it for low stakes
work. Setup and the full picture: [FREE_TIER_ROUTER.md](FREE_TIER_ROUTER.md).

### What it costs

`/usage` in chat, or `python main.py usage 30`, shows calls, tokens and cost per brain.
`/trace` shows tokens and cost for each step of a run. When GawdBot works a Paperclip task,
its spend is reported to Paperclip so company budgets see it (unknown prices are reported
as "unpriced", never guessed).

## Memory: what GawdBot remembers

Every memory has a kind:

| Kind | Example | Kept |
|---|---|---|
| fact | "The mixing engineer is Dre" | until deleted |
| preference | "Sun wants plain prose, no dashes" | until deleted |
| decision | "We're releasing the single before the album" | until deleted |
| deadline | "Coursera module due" with a due date | until deleted |
| task | "Send stems to Dre" with an optional due date | until deleted |
| note | anything else worth keeping | until deleted |
| exchange | raw chat turns, saved automatically | `MEMORY_EXCHANGE_DAYS` (90) |

**Search uses two methods at once.** Keyword search (SQLite FTS5, always on, no API) and
meaning search (embeddings, when an embedding brain is available). Results both methods
agree on rise to the top. Saved facts outrank old chat.

**Recall is automatic.** Each run starts with the most relevant saved memories and anything
due in the next week, so GawdBot remembers without being asked.

**Switching embedding providers is safe.** Each vector records which model made it, and
vectors from different models are never compared. After switching, run
`python main.py memory reembed`.

### The memory graph

Memories are also connected to the **people, projects and things** they are about. Ideas
adapted from [Graphify](https://github.com/Graphify-Labs/graphify) (MIT / Apache 2.0),
rebuilt on the same SQLite file with no new dependencies.

- When GawdBot saves a memory it lists who and what it is about (`about=["Dre", "Sid Vicious single"]`).
  Any person or project it already knows is linked automatically when named later.
- Links between things carry a name and an honesty label:
  - **EXTRACTED**: both names appear in the memory that supports the link
  - **INFERRED**: deduced, e.g. two things mentioned together, or a link with no supporting text
  New evidence can upgrade INFERRED to EXTRACTED. Nothing ever downgrades it.
- **Search gets a third voter.** From the best keyword and meaning matches, GawdBot follows
  the graph to other memories about the same people and projects. That finds memories that
  share no words with your question ("who is mixing the single?" also finds "his studio rate
  is 150 an hour").
- **Hubs are never stepping stones.** Something mentioned almost everywhere (like "Sun")
  would connect everything to everything, so it is only used when the question names it.
- New questions: "what do I know about Dre" (`memory_about`) and "how is Dre connected to
  LearningMate" (`memory_connect`), with the evidence for every hop.
- Forgetting a memory removes the links it alone supported, and anything left pointing at
  nothing is cleaned up.

Measured: 5,000 memories and 500 people and projects, about 5 ms to save and 15 ms to search
(44 ms worst case), graph included.

**Why not install Graphify itself?** It maps code and document folders and pulls in parsers
for about 40 languages plus a graph library. Its own conversational memory benchmark shows
plain hybrid search (what we already had) within 2 points of Graphify, with graph expansion
adding a small edge. So we took the parts that create that edge. Graphify stays a good
candidate later for mapping codebases and document folders, through its MCP server.

### Commands

```
python main.py memory about <name>
python main.py memory connect <a> -- <b>
python main.py memory                    counts by kind, which models made the vectors
python main.py memory search <words>
python main.py memory upcoming [days]
python main.py memory forget <id>
python main.py memory prune [days]       old chat exchanges only; saved facts are never pruned
python main.py memory reembed
```

### Upgrading from the old memory

The first time the new code opens `data/memory.db`, old memories are copied into the new
format and the old table is renamed to `memories_legacy`. Nothing is deleted. Old vectors
are not reused because nobody recorded which model made them; run `memory reembed` to
rebuild them.

### Privacy

Memory lives in `data/memory.db` on your machine. The agent's file and shell tools cannot
read that folder. Text you save is sent to your embedding brain to make vectors; use the
`local-embed` brain (Ollama) if you want that to stay on your machine too. The database is
not encrypted yet; full disk encryption (BitLocker on Windows) covers it in the meantime.
