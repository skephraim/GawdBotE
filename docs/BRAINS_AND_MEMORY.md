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

### Commands

```
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
