# Lesson 07 — Memory

## The Problem: LLMs Are Stateless

Every time you call an LLM API, it starts with a blank slate. Send 1000 messages to it and it still won't remember what you talked about yesterday — unless you explicitly include that history in the prompt.

For a 24/7 AI assistant, this is a problem. You want it to remember:
- "My name is Alex"
- "I prefer dark mode"
- "We discussed the auth bug on Tuesday"
- "The API key is stored in /etc/secrets"

GawdBotE stores these as **memories** in a SQLite database and retrieves relevant ones before responding.

---

## Two Approaches to Memory

**Naive approach:** Just prepend the last N messages to every prompt.
- Simple, but expensive (lots of tokens)
- Retrieves irrelevant old messages

**Semantic search approach:** Store memories as vectors, retrieve only the *relevant* ones.
- More efficient
- Gets the right memories even if they use different words

GawdBotE uses semantic search. Here's how.

---

## Embeddings — Text as Numbers

An **embedding** is a list of floating-point numbers that represents the *meaning* of a piece of text. The key property:

> **Similar meanings → similar numbers (vectors that point in the same direction)**

```
"I love dogs"     → [0.2, 0.8, -0.1, 0.5, ...]   (768 numbers)
"I enjoy cats"    → [0.2, 0.7, -0.1, 0.4, ...]   (very similar!)
"The sky is blue" → [-0.3, 0.1, 0.9, -0.2, ...]  (very different)
```

This means: to find memories relevant to "what pets do you prefer?", you get the embedding for that question, then find stored memories whose embeddings are *close to* that vector.

---

## Cosine Similarity

"Closeness" between two vectors is measured with **cosine similarity**:

```python
def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a))
    nb  = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0
```

- Returns a value from -1 to 1
- 1.0 = identical meaning
- 0.0 = unrelated
- -1.0 = opposite meaning

It's called "cosine" because it measures the *angle* between two vectors, not their distance. This is better for text because it's length-independent.

---

## The Full Memory System

### Storing a Memory

```python
async def store(content: str, source: str = "agent") -> int:
    vec = await _embed(content)          # get the embedding vector
    with _conn() as db:
        cur = db.execute(
            "INSERT INTO memories (ts, source, content, embedding) VALUES (?, ?, ?, ?)",
            (time.time(), source, content, json.dumps(vec))  # store vector as JSON
        )
        return cur.lastrowid
```

Every time the agent responds to a message, it automatically stores the exchange:
```python
await memory.store(f"[{source}] User: {user_message}\nAssistant: {final}")
```

### Searching Memories

```python
async def search(query: str, top_k=5) -> list[dict]:
    qvec = await _embed(query)           # embed the search query
    with _conn() as db:
        rows = db.execute("SELECT id, ts, source, content, embedding FROM memories").fetchall()

    scored = []
    for row in rows:
        emb = json.loads(row[4])         # deserialize the stored vector
        score = _cosine(qvec, emb)       # compare against query vector
        scored.append({..., "score": score})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]                # return top 5 most relevant
```

### Why SQLite?

Most vector memory systems require a separate database like Pinecone, Weaviate, or Chroma. GawdBotE uses plain SQLite because:

- **Zero dependencies** — SQLite is built into Python
- **No server needed** — it's just a file (`data/memory.db`)
- **Works great at personal scale** — thousands of memories is fine; millions might need indexing

The tradeoff: loading all embeddings to compute similarity is O(n). For 10,000 memories it's still fast (milliseconds). For millions you'd add an index — but GawdBotE is a personal assistant, not enterprise software.

---

## Getting Embeddings

The `_embed()` function tries providers in order (similar to the LLM fallback):

```python
async def _embed(text: str) -> list[float]:
    for provider in config.LLM_PROVIDERS:
        try:
            if provider == "nvidia" and config.NVIDIA_API_KEY:
                # NVIDIA NIM embedding model
                resp = await client.embeddings.create(model="nvidia/nv-embedqa-e5-v5", input=text)
                return resp.data[0].embedding         # list of ~4096 floats

            if provider == "ollama":
                # Ollama embedding model
                resp = await client.embeddings.create(model="nomic-embed-text", input=text)
                return resp.data[0].embedding         # list of ~768 floats

            if provider == "openai" and config.OPENAI_API_KEY:
                resp = await client.embeddings.create(model="text-embedding-3-small", input=text)
                return resp.data[0].embedding
        except Exception as e:
            continue

    # Last resort: keyword-based fallback (not semantic, but better than nothing)
    words = text.lower().split()
    vocab = sorted(set(words))
    return [words.count(w) / len(words) for w in vocab]
```

The fallback at the end is a **bag-of-words** vector — it counts word frequency instead of capturing semantic meaning. Not great, but it means memory still works even when no embedding API is available.

---

## The Database Schema

```sql
CREATE TABLE IF NOT EXISTS memories (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL    NOT NULL,        -- Unix timestamp
    source    TEXT    NOT NULL,        -- "telegram", "voice", "cron", etc.
    content   TEXT    NOT NULL,        -- the actual memory text
    embedding TEXT    NOT NULL         -- JSON array of floats
);
```

SQLite can store the embedding vector as a JSON string. Simple and portable.

---

## Exercise

Open a Python shell in the project directory:

```python
import asyncio
from core import memory

async def test():
    # Store some memories
    await memory.store("My favorite language is Python")
    await memory.store("I have a cat named Luna")
    await memory.store("The project uses SQLite for the database")

    # Search for relevant memories
    results = await memory.search("what database do we use?")
    for r in results:
        print(f"[{r['score']:.3f}] {r['content']}")

asyncio.run(test())
```

Notice how the score for "The project uses SQLite" is much higher than "I have a cat named Luna" even though neither uses the exact words from the query.

---

## Key Takeaways

- LLMs are stateless — memory must be stored and retrieved explicitly
- **Embeddings** turn text into vectors where similar meanings are numerically close
- **Cosine similarity** measures how similar two vectors are (0=unrelated, 1=identical)
- GawdBotE stores embeddings in **SQLite** — no external vector database needed
- Memory is retrieved by semantic similarity, not keyword match
- GawdBotE automatically stores every conversation exchange as a memory
