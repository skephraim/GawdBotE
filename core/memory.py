"""
Long term memory: structured, searchable, and stored on your own disk (data/memory.db).

Each memory has a KIND, so it can be found and handled the right way:
  fact, preference, decision, deadline, task, note   kept until you delete them
  exchange                                            raw chat turns, pruned after MEMORY_EXCHANGE_DAYS

Search uses two methods at once and blends the results:
  1. Keyword search with SQLite FTS5 (built into Python). Always works, no API needed.
     Porter stemming means "deadlines" also finds "deadline".
  2. Meaning search with embeddings (vectors), when an embedding brain is available.
     Every vector is tagged with the model that made it, and only vectors from the SAME
     model are ever compared. Switching providers no longer breaks search; run
     `python main.py memory reembed` to bring old memories onto the new model.

  3. Graph expansion (core/graph.py), adapted from Graphify: from the best results, follow
     the people and projects they mention to OTHER memories about them, never walking
     through "hub" entities that touch everything.

The ranked lists are blended with Reciprocal Rank Fusion: each list votes 1 / (60 + rank)
for its results and the votes are added up. A memory found by several methods rises to the
top. Think of independent clinicians ranking a differential diagnosis: what they agree on
near the top is probably right.

At the start of every run the agent is handed the few most relevant durable memories and
any deadlines coming up, so it remembers without being asked to search.
"""
from __future__ import annotations

import logging
import math
import re
import sqlite3
import time
from array import array
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import config
from core import graph

log = logging.getLogger(__name__)

KINDS = ("fact", "preference", "decision", "deadline", "task", "note", "exchange")
DURABLE_KINDS = tuple(k for k in KINDS if k != "exchange")
SCHEMA_VERSION = 2          # 1: memory_items + FTS   2: + entity graph
RRF_K = 60
GRAPH_DEPTH = 1            # hops to walk from the seed memories' entities
MAX_CONTENT_CHARS = 20_000

_kinds_sql = ", ".join(f"'{k}'" for k in KINDS)
_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS memory_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    kind        TEXT    NOT NULL CHECK (kind IN ({_kinds_sql})),
    content     TEXT    NOT NULL,
    source      TEXT    NOT NULL DEFAULT 'agent',
    due_ts      REAL,
    pinned      INTEGER NOT NULL DEFAULT 0,
    embed_model TEXT,
    vec         BLOB
);
CREATE INDEX IF NOT EXISTS idx_memory_kind_ts ON memory_items(kind, ts);
CREATE INDEX IF NOT EXISTS idx_memory_due     ON memory_items(due_ts) WHERE due_ts IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_memory_model   ON memory_items(embed_model) WHERE embed_model IS NOT NULL;
"""

# An "external content" FTS index: the text lives once in memory_items, and triggers keep
# the search index in step with every insert, delete and edit automatically.
_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
    USING fts5(content, content='memory_items', content_rowid='id', tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory_items BEGIN
    INSERT INTO memory_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory_items BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE OF content ON memory_items BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO memory_fts(rowid, content) VALUES (new.id, new.content);
END;
"""

_has_fts: Optional[bool] = None


def _db_path() -> Path:
    return Path(config.MEMORY_DB)


@contextmanager
def _db():
    """Open the memory database, make sure the tables exist, commit on success, always close."""
    global _has_fts
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        # SQLite ships with foreign keys OFF for backward compatibility. Turning them on per
        # connection is what makes ON DELETE CASCADE actually clean up the graph.
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(_SCHEMA)
        conn.executescript(graph.SCHEMA)
        try:
            conn.executescript(_FTS)
            _has_fts = True
        except sqlite3.OperationalError as exc:      # very old SQLite without FTS5
            if _has_fts is not False:
                log.warning("SQLite FTS5 unavailable (%s); keyword search falls back to LIKE", exc)
            _has_fts = False
        _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """
    One time upgrade from the original `memories` table. PRAGMA user_version is a number
    SQLite keeps in the file header for exactly this: "which schema version is this file?"
    Old rows are copied, then the old table is RENAMED, never deleted, so nothing is lost.
    Old vectors are not copied because nobody recorded which model made them;
    `python main.py memory reembed` rebuilds them.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    if version >= 1:
        # 1 -> 2: the graph tables were just created by graph.SCHEMA; existing memories are
        # untouched and join the graph as entities get named in new memories.
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        return
    old = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memories'").fetchone()
    if old:
        n = conn.execute(
            """INSERT INTO memory_items (ts, kind, content, source)
               SELECT ts,
                      CASE WHEN source IN ('agent', 'self_evolve') THEN 'note' ELSE 'exchange' END,
                      content, source
               FROM memories"""
        ).rowcount
        conn.execute("ALTER TABLE memories RENAME TO memories_legacy")
        log.info("Memory upgraded: %d old memories moved into the new format", n)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


# ── Vectors ────────────────────────────────────────────────────────────────────

def _pack(vec: Iterable[float]) -> bytes:
    """Normalize to length 1 and store as 4 byte floats. Normalized vectors make cosine similarity a plain dot product."""
    vec = list(vec)
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return array("f", (x / norm for x in vec)).tobytes()


def _unpack(blob: bytes) -> array:
    a = array("f")
    a.frombytes(blob)
    return a


async def _embed_one(text: str, purpose: str) -> Optional[tuple[bytes, str]]:
    from core import llm
    try:
        result = await llm.embed([text], purpose)
    except Exception as exc:
        log.warning("Embedding failed: %s", exc)
        return None
    if not result:
        return None
    vectors, model = result
    return _pack(vectors[0]), model


# ── Writing ────────────────────────────────────────────────────────────────────

async def store(content: str, source: str = "agent", kind: str = "note",
                due_ts: Optional[float] = None, pinned: bool = False,
                about: Optional[Iterable[str]] = None) -> int:
    """
    Save a memory and return its id. Saving the same durable memory twice returns the first id.
    about: names of the people, projects or things this memory is about. Known entities named
    in the text are linked automatically as well.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    content = (content or "").strip()[:MAX_CONTENT_CHARS]
    if not content:
        raise ValueError("cannot store an empty memory")

    if kind != "exchange":
        with _db() as db:
            dup = db.execute("SELECT id FROM memory_items WHERE kind = ? AND content = ?", (kind, content)).fetchone()
            if dup:
                graph.link_memory(db, dup["id"], content, about or [])
        if dup:
            return dup["id"]

    vec = model = None
    if kind != "exchange" or config.MEMORY_EMBED_EXCHANGES:
        embedded = await _embed_one(content, "passage")
        if embedded:
            vec, model = embedded

    with _db() as db:
        cur = db.execute(
            "INSERT INTO memory_items (ts, kind, content, source, due_ts, pinned, embed_model, vec) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), kind, content, source, due_ts, int(pinned), model, vec),
        )
        graph.link_memory(db, cur.lastrowid, content, about or [])
        return cur.lastrowid


def forget(memory_id: int) -> bool:
    """Delete one memory. Returns True if it existed."""
    with _db() as db:
        gone = db.execute("DELETE FROM memory_items WHERE id = ?", (int(memory_id),)).rowcount > 0
        graph.cleanup(db)       # mentions and relations went with it (CASCADE); drop orphan entities
        return gone


def delete(memory_id: int) -> str:     # kept for older callers
    return f"Memory {memory_id} deleted." if forget(memory_id) else f"No memory {memory_id}."


def prune_exchanges(days: Optional[int] = None) -> int:
    """Delete raw chat exchanges older than N days. Durable memories are never pruned."""
    days = config.MEMORY_EXCHANGE_DAYS if days is None else days
    cutoff = time.time() - days * 86_400
    with _db() as db:
        n = db.execute("DELETE FROM memory_items WHERE kind = 'exchange' AND ts < ? AND pinned = 0",
                       (cutoff,)).rowcount
        graph.cleanup(db)
        return n


# ── Searching ──────────────────────────────────────────────────────────────────

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "to", "of", "in", "on", "for", "and", "or",
    "what", "who", "when", "where", "how", "do", "does", "did", "i", "me", "my", "you", "your",
    "it", "that", "this", "with", "about", "can", "could", "should", "would", "we", "our",
}


def _kind_filter(kinds: Optional[Iterable[str]], alias: str = "") -> tuple[str, list]:
    if not kinds:
        return "", []
    kinds = [k for k in kinds if k in KINDS]
    col = f"{alias}kind" if alias else "kind"
    return f" AND {col} IN ({', '.join('?' * len(kinds))})", kinds


def _keyword_ranks(db: sqlite3.Connection, query: str, kinds, limit: int) -> list[int]:
    terms = [t for t in re.findall(r"\w+", query.lower()) if t not in _STOPWORDS][:20]
    if not terms:
        return []
    where, params = _kind_filter(kinds, "m.")
    if _has_fts:
        # Quote every term so punctuation in the question can never break FTS query syntax.
        match = " OR ".join(f'"{t}"' for t in terms)
        rows = db.execute(
            f"""SELECT m.id FROM memory_fts f JOIN memory_items m ON m.id = f.rowid
                WHERE memory_fts MATCH ? {where}
                ORDER BY bm25(memory_fts) LIMIT ?""",
            [match, *params, limit],
        ).fetchall()
    else:
        likes = " OR ".join("m.content LIKE ?" for _ in terms)
        rows = db.execute(
            f"SELECT m.id FROM memory_items m WHERE ({likes}) {where} ORDER BY m.ts DESC LIMIT ?",
            [*[f"%{t}%" for t in terms], *params, limit],
        ).fetchall()
    return [r["id"] for r in rows]


async def _vector_ranks(query: str, kinds, limit: int) -> list[int]:
    embedded = await _embed_one(query, "query")
    if not embedded:
        return []
    qvec, model = _unpack(embedded[0]), embedded[1]
    where, params = _kind_filter(kinds)
    with _db() as db:
        rows = db.execute(f"SELECT id, vec FROM memory_items WHERE embed_model = ? AND vec IS NOT NULL {where}",
                          [model, *params]).fetchall()
    scored = []
    for r in rows:
        v = _unpack(r["vec"])
        if len(v) == len(qvec):
            scored.append((sum(a * b for a, b in zip(qvec, v)), r["id"]))
    scored.sort(reverse=True)
    return [mid for score, mid in scored[:limit] if score > 0.2]


def _rows_by_id(ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    with _db() as db:
        rows = db.execute(
            f"SELECT id, ts, kind, content, source, due_ts, pinned FROM memory_items WHERE id IN ({', '.join('?' * len(ids))})",
            ids,
        ).fetchall()
    return {r["id"]: dict(r) for r in rows}


async def search(query: str, top_k: Optional[int] = None, kinds: Optional[Iterable[str]] = None) -> list[dict]:
    """Hybrid keyword plus meaning search. Returns dicts with id, kind, content, ts, due_ts, score, matched."""
    top_k = top_k or config.MEMORY_MAX_RESULTS
    pool = max(top_k * 5, 25)
    kinds = list(kinds) if kinds else None
    with _db() as db:
        keyword = _keyword_ranks(db, query, kinds, pool)
    meaning = await _vector_ranks(query, kinds, pool)

    # Seeds for the graph walk: the strongest direct hits from either method.
    seeds = list(dict.fromkeys(keyword[:3] + meaning[:3]))
    with _db() as db:
        connected = graph.expand(db, seeds, query, depth=GRAPH_DEPTH, limit=pool, kinds=kinds)

    votes: dict[int, float] = {}
    how: dict[int, set] = {}
    for label, ranked in (("keyword", keyword), ("meaning", meaning), ("graph", connected)):
        for rank, mid in enumerate(ranked, start=1):
            votes[mid] = votes.get(mid, 0.0) + 1.0 / (RRF_K + rank)
            how.setdefault(mid, set()).add(label)

    rows = _rows_by_id(list(votes))
    results = []
    for mid, score in votes.items():
        row = rows.get(mid)
        if not row:
            continue
        if row["kind"] == "exchange":
            score *= 0.6          # a saved fact beats a stray line of old chat
        if row["pinned"]:
            score *= 1.5
        row["score"] = score
        row["matched"] = "+".join(sorted(how[mid]))
        results.append(row)
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:top_k]


def upcoming(days: int = 7, overdue_days: int = 30) -> list[dict]:
    """Deadlines and tasks due in the next N days, plus anything overdue within the last month."""
    now = time.time()
    with _db() as db:
        rows = db.execute(
            """SELECT id, ts, kind, content, source, due_ts, pinned FROM memory_items
               WHERE due_ts IS NOT NULL AND due_ts BETWEEN ? AND ?
               ORDER BY due_ts""",
            (now - overdue_days * 86_400, now + days * 86_400),
        ).fetchall()
    return [dict(r) for r in rows]


async def recall(message: str, k: Optional[int] = None) -> list[dict]:
    """What the agent should have in mind for this message: relevant durable memories plus upcoming deadlines."""
    k = config.MEMORY_RECALL_K if k is None else k
    if k <= 0:
        return []
    found = await search(message, top_k=k, kinds=DURABLE_KINDS)
    seen = {r["id"] for r in found}
    return found + [r for r in upcoming(7) if r["id"] not in seen][:3]


def recent(n: int = 10) -> list[dict]:
    with _db() as db:
        rows = db.execute("SELECT id, ts, kind, content, source, due_ts FROM memory_items ORDER BY ts DESC LIMIT ?", (n,)).fetchall()
    return [dict(r) for r in rows]


# ── Maintenance ────────────────────────────────────────────────────────────────

def stats() -> dict:
    with _db() as db:
        by_kind = {r["kind"]: r["n"] for r in db.execute("SELECT kind, COUNT(*) AS n FROM memory_items GROUP BY kind")}
        models = {r["embed_model"]: r["n"] for r in db.execute(
            "SELECT embed_model, COUNT(*) AS n FROM memory_items WHERE embed_model IS NOT NULL GROUP BY embed_model")}
        legacy = db.execute("SELECT 1 FROM sqlite_master WHERE name = 'memories_legacy'").fetchone() is not None
        graph_stats = graph.stats(db)
    return {"by_kind": by_kind, "total": sum(by_kind.values()), "embedded_by_model": models,
            "keyword_index": "fts5" if _has_fts else "like", "legacy_table_kept": legacy, "graph": graph_stats}


async def reembed(batch: int = 32) -> tuple[int, Optional[str]]:
    """Give every durable memory a vector from the CURRENT embedding model. Returns (count, model)."""
    from core import llm
    probe = await llm.embed(["probe"], "passage")
    if not probe:
        return 0, None
    model = probe[1]
    kinds = KINDS if config.MEMORY_EMBED_EXCHANGES else DURABLE_KINDS
    where, params = _kind_filter(kinds)
    with _db() as db:
        todo = db.execute(
            f"SELECT id, content FROM memory_items WHERE (embed_model IS NULL OR embed_model != ?) {where}",
            [model, *params],
        ).fetchall()
    done = 0
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        result = await llm.embed([r["content"] for r in chunk], "passage")
        if not result or result[1] != model:
            break        # the embedder changed or failed mid run: stop rather than mix models
        with _db() as db:
            db.executemany("UPDATE memory_items SET vec = ?, embed_model = ? WHERE id = ?",
                           [(_pack(v), model, r["id"]) for v, r in zip(result[0], chunk)])
        done += len(chunk)
    return done, model


# ── Formatting ─────────────────────────────────────────────────────────────────

def _day(ts: Optional[float]) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""


def format_results(rows: list[dict], width: int = 300) -> str:
    if not rows:
        return "No memories found."
    out = []
    for r in rows:
        text = " ".join(r["content"].split())
        text = text if len(text) <= width else text[:width] + "..."
        due = f" (due {_day(r['due_ts'])})" if r.get("due_ts") else ""
        out.append(f"#{r['id']} [{r['kind']}, {_day(r['ts'])}]{due} {text}")
    return "\n".join(out)


def format_for_prompt(rows: list[dict]) -> str:
    if not rows:
        return ""
    return (
        "\n\n## What you remember\n"
        "From your own memory database. It may be out of date; trust the owner's newest words over it.\n"
        + format_results(rows, width=500)
    )


# ── Graph questions ────────────────────────────────────────────────────────────

def link(subject: str, relation: str, obj: str, memory_id: Optional[int] = None) -> str:
    """Record a named link between two things. Returns how confident the link is."""
    with _db() as db:
        return graph.add_relation(db, subject, relation, obj, memory_id)


def about_text(name: str) -> str:
    """A readable card of everything known about one person, project or thing."""
    with _db() as db:
        card = graph.about(db, name)
    if card is None:
        return f"I have nothing filed about {name!r}."
    lines = [f"{card['name']}: mentioned in {card['memory_count']} memories"]
    if card["links"]:
        lines.append("Links:")
        for l in card["links"]:
            arrow = f"{card['name']} {l['relation']} {l['other']}" if l["direction"] == "out" \
                else f"{l['other']} {l['relation']} {card['name']}"
            src = f", from memory #{l['memory_id']}" if l["memory_id"] else ""
            lines.append(f"  {arrow} [{l['confidence']}{src}]")
    if card["mentioned_with"]:
        lines.append("Often mentioned with: " + ", ".join(f"{r['name']} ({r['n']})" for r in card["mentioned_with"]))
    if card["memories"]:
        lines.append("Memories:")
        lines.append(format_results(card["memories"], width=200))
    return "\n".join(lines)


def connect_text(a: str, b: str) -> str:
    """How two things are connected, hop by hop, with the evidence for each hop."""
    with _db() as db:
        steps = graph.connect(db, a, b)
    if steps is None:
        return f"I can't find a connection between {a!r} and {b!r} in memory."
    if not steps:
        return f"{a!r} and {b!r} are the same thing in memory."
    lines = [f"{len(steps)} hop{'s' if len(steps) != 1 else ''}:"]
    for st in steps:
        src = f", memory #{st['memory_id']}" if st["memory_id"] else ""
        lines.append(f"  {st['from']} --{st['relation']}--> {st['to']} [{st['confidence']}{src}]")
    return "\n".join(lines)
