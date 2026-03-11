"""
Persistent memory: SQLite + cosine-similarity search.
No external vector DB required.
"""
from __future__ import annotations
import asyncio
import json
import logging
import math
import sqlite3
import time
from pathlib import Path
from typing import Optional

import config

log = logging.getLogger(__name__)

_DB_PATH = Path(config.MEMORY_DB)
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL    NOT NULL,
    source    TEXT    NOT NULL DEFAULT 'agent',
    content   TEXT    NOT NULL,
    embedding TEXT    NOT NULL
);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH)
    c.executescript(_SCHEMA)
    return c


# ── Embeddings ─────────────────────────────────────────────────────────────────

async def _embed(text: str) -> list[float]:
    """Generate an embedding vector via the first available provider."""
    for provider in config.LLM_PROVIDERS:
        try:
            if provider == "nvidia" and config.NVIDIA_API_KEY:
                from openai import AsyncOpenAI
                client = AsyncOpenAI(base_url=config.NVIDIA_BASE_URL, api_key=config.NVIDIA_API_KEY)
                model = "nvidia/nv-embedqa-e5-v5"
                resp = await client.embeddings.create(model=model, input=text, encoding_format="float",
                                                       extra_body={"input_type": "passage"})
                return resp.data[0].embedding
            if provider == "ollama":
                from openai import AsyncOpenAI
                client = AsyncOpenAI(base_url=config.OLLAMA_BASE_URL, api_key="ollama")
                resp = await client.embeddings.create(model="nomic-embed-text", input=text)
                return resp.data[0].embedding
            if provider == "openai" and config.OPENAI_API_KEY:
                from openai import AsyncOpenAI
                client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
                resp = await client.embeddings.create(model="text-embedding-3-small", input=text)
                return resp.data[0].embedding
        except Exception as e:
            log.warning("Embedding provider %r failed: %s", provider, e)

    # Fallback: simple bag-of-words vector (deterministic, not semantic)
    log.warning("No embedding provider available — using keyword fallback")
    words = text.lower().split()
    vocab = sorted(set(words))
    if not vocab:
        return [0.0]
    vec = [words.count(w) / len(words) for w in vocab]
    return vec


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


# ── Public API ─────────────────────────────────────────────────────────────────

async def store(content: str, source: str = "agent") -> int:
    """Store a memory and return its id."""
    vec = await _embed(content)
    with _conn() as db:
        cur = db.execute(
            "INSERT INTO memories (ts, source, content, embedding) VALUES (?, ?, ?, ?)",
            (time.time(), source, content, json.dumps(vec)),
        )
        return cur.lastrowid


async def search(query: str, top_k: int = None) -> list[dict]:
    """Return the top-k most relevant memories for a query."""
    top_k = top_k or config.MEMORY_MAX_RESULTS
    qvec = await _embed(query)
    with _conn() as db:
        rows = db.execute("SELECT id, ts, source, content, embedding FROM memories").fetchall()

    scored = []
    for row in rows:
        emb = json.loads(row[4])
        score = _cosine(qvec, emb)
        scored.append({"id": row[0], "ts": row[1], "source": row[2], "content": row[3], "score": score})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


def recent(n: int = 10) -> list[dict]:
    """Return the n most recent memories."""
    with _conn() as db:
        rows = db.execute(
            "SELECT id, ts, source, content FROM memories ORDER BY ts DESC LIMIT ?", (n,)
        ).fetchall()
    return [{"id": r[0], "ts": r[1], "source": r[2], "content": r[3]} for r in rows]


def delete(memory_id: int) -> str:
    with _conn() as db:
        db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    return f"Memory {memory_id} deleted."
