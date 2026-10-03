"""
Memory graph: the people, projects and things in your memories, and how they connect.

Ideas adapted from Graphify (github.com/Graphify-Labs/graphify, MIT / Apache 2.0), rebuilt
on SQLite with no new dependencies:

  1. Seed, then expand. Search finds the best memories directly. Then we follow the graph:
     the entities those memories mention, what those entities connect to, and the OTHER
     memories about them. That finds memories that share no words with the question.
  2. Don't walk through hubs. An entity mentioned almost everywhere ("Sun") would connect
     everything to everything, so it is never used as a stepping stone unless the question
     names it directly.
  3. Every link says how we know it:
       EXTRACTED  both names appear in the memory text that supports the link
       INFERRED   deduced: entities mentioned together, or a link with no supporting text
     Like charting "patient states" versus "clinician suspects".

Tables (in data/memory.db, next to memory_items):
  entities   one row per thing, unique by normalized name
  mentions   which memories mention which entities          (memory  <-> entity)
  relations  named links between entities, with confidence   (entity  -> entity)

Deleting a memory deletes its mentions and the relations it supported (ON DELETE CASCADE),
then entities that nothing refers to any more are removed.
"""
from __future__ import annotations

import re
import sqlite3
import time
from collections import deque
from typing import Iterable, Optional

MAX_NAME_CHARS = 120
MAX_ABOUT = 20
HUB_FLOOR = 25            # an entity needs at least this many links before it can count as a hub

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    norm       TEXT    NOT NULL UNIQUE,
    created_ts REAL    NOT NULL
);
CREATE TABLE IF NOT EXISTS mentions (
    memory_id INTEGER NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(id)     ON DELETE CASCADE,
    PRIMARY KEY (memory_id, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_mentions_entity ON mentions(entity_id);
CREATE TABLE IF NOT EXISTS relations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    src        INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    dst        INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relation   TEXT    NOT NULL,
    confidence TEXT    NOT NULL CHECK (confidence IN ('EXTRACTED', 'INFERRED')),
    memory_id  INTEGER REFERENCES memory_items(id) ON DELETE CASCADE,
    ts         REAL    NOT NULL,
    UNIQUE (src, dst, relation)
);
CREATE INDEX IF NOT EXISTS idx_relations_src ON relations(src);
CREATE INDEX IF NOT EXISTS idx_relations_dst ON relations(dst);

-- Every way two entities are connected, in both directions, in one place.
CREATE VIEW IF NOT EXISTS entity_edges AS
    SELECT src AS a, dst AS b, relation, confidence, memory_id, 1 AS explicit FROM relations
    UNION ALL
    SELECT dst AS a, src AS b, relation, confidence, memory_id, 1 AS explicit FROM relations
    UNION ALL
    SELECT m1.entity_id AS a, m2.entity_id AS b, 'mentioned with' AS relation,
           'INFERRED' AS confidence, m1.memory_id, 0 AS explicit
    FROM mentions m1
    JOIN mentions m2 ON m1.memory_id = m2.memory_id AND m1.entity_id <> m2.entity_id;
"""


def norm(text: str) -> str:
    """Lowercase words joined by single spaces: 'Sid  Vicious!' and 'sid vicious' are the same entity."""
    return " ".join(re.findall(r"\w+", (text or "").lower()))


def _padded(text: str) -> str:
    # Spaces on both ends turn "is this exact word sequence present" into a plain substring
    # test that respects word boundaries: " dre " is not inside " andre ".
    return f" {norm(text)} "


def clean_name(name: str) -> str:
    name = " ".join((name or "").split())[:MAX_NAME_CHARS]
    if not norm(name):
        raise ValueError("an entity needs a name with at least one letter or number")
    return name


# ── Writing ────────────────────────────────────────────────────────────────────

def upsert_entity(db: sqlite3.Connection, name: str) -> int:
    name = clean_name(name)
    db.execute("INSERT INTO entities (name, norm, created_ts) VALUES (?, ?, ?) ON CONFLICT(norm) DO NOTHING",
               (name, norm(name), time.time()))
    return db.execute("SELECT id FROM entities WHERE norm = ?", (norm(name),)).fetchone()[0]


def entities_in_text(db: sqlite3.Connection, text: str) -> list[int]:
    """Ids of known entities whose full name appears in the text as whole words."""
    hay = _padded(text)
    return [r[0] for r in db.execute("SELECT id, norm FROM entities") if f" {r[1]} " in hay]


def link_memory(db: sqlite3.Connection, memory_id: int, text: str, about: Iterable[str] = ()) -> list[int]:
    """Connect a memory to the entities it is about, plus any known entity it names."""
    ids = [upsert_entity(db, n) for n in list(about)[:MAX_ABOUT] if norm(n)]
    ids += entities_in_text(db, text)
    ids = list(dict.fromkeys(ids))
    db.executemany("INSERT OR IGNORE INTO mentions (memory_id, entity_id) VALUES (?, ?)", [(memory_id, e) for e in ids])
    return ids


def add_relation(db: sqlite3.Connection, subject: str, relation: str, obj: str,
                 memory_id: Optional[int] = None) -> str:
    """
    Record "subject relation object", e.g. "Dre / mixes / Sid Vicious single".
    Confidence is decided by evidence, not by whoever asks: EXTRACTED only when both names
    literally appear in the supporting memory's text.
    """
    relation = " ".join(norm(relation).split()[:8])
    if not relation:
        raise ValueError("a relation needs a name, e.g. 'mixes' or 'manages'")
    src, dst = upsert_entity(db, subject), upsert_entity(db, obj)
    if src == dst:
        raise ValueError("an entity cannot be related to itself")

    confidence = "INFERRED"
    if memory_id is not None:
        row = db.execute("SELECT content FROM memory_items WHERE id = ?", (memory_id,)).fetchone()
        if row is None:
            raise ValueError(f"no memory #{memory_id}")
        hay = _padded(row[0])
        if f" {norm(subject)} " in hay and f" {norm(obj)} " in hay:
            confidence = "EXTRACTED"
        db.executemany("INSERT OR IGNORE INTO mentions (memory_id, entity_id) VALUES (?, ?)",
                       [(memory_id, src), (memory_id, dst)])

    # UPSERT: if the link already exists, keep it, but upgrade INFERRED to EXTRACTED when
    # new evidence arrives. Evidence can raise confidence; it never lowers it.
    db.execute(
        """INSERT INTO relations (src, dst, relation, confidence, memory_id, ts)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(src, dst, relation) DO UPDATE SET
               confidence = CASE WHEN excluded.confidence = 'EXTRACTED' THEN 'EXTRACTED' ELSE relations.confidence END,
               memory_id  = CASE WHEN excluded.confidence = 'EXTRACTED' THEN excluded.memory_id
                                 ELSE COALESCE(relations.memory_id, excluded.memory_id) END""",
        (src, dst, relation, confidence, memory_id, time.time()),
    )
    return db.execute("SELECT confidence FROM relations WHERE src = ? AND dst = ? AND relation = ?",
                      (src, dst, relation)).fetchone()[0]


def cleanup(db: sqlite3.Connection) -> int:
    """Remove entities nothing points to any more. Returns how many were removed."""
    return db.execute(
        """DELETE FROM entities
           WHERE id NOT IN (SELECT entity_id FROM mentions)
             AND id NOT IN (SELECT src FROM relations)
             AND id NOT IN (SELECT dst FROM relations)"""
    ).rowcount


# ── Reading the shape of the graph ─────────────────────────────────────────────

def find_entity(db: sqlite3.Connection, name: str) -> Optional[sqlite3.Row]:
    n = norm(name)
    if not n:
        return None
    row = db.execute("SELECT id, name FROM entities WHERE norm = ?", (n,)).fetchone()
    if row:
        return row
    # Fall back to a partial match on whole words, shortest name first ("Dre" for "dre lee"?)
    return db.execute("SELECT id, name FROM entities WHERE (' ' || norm || ' ') LIKE ? ORDER BY length(norm) LIMIT 1",
                      (f"% {n} %",)).fetchone()


def degrees(db: sqlite3.Connection) -> dict[int, int]:
    """How many memories and links touch each entity."""
    deg: dict[int, int] = {}
    for eid, n in db.execute("SELECT entity_id, COUNT(*) FROM mentions GROUP BY entity_id"):
        deg[eid] = deg.get(eid, 0) + n
    for eid, n in db.execute("SELECT a, COUNT(*) FROM entity_edges WHERE explicit = 1 GROUP BY a"):
        deg[eid] = deg.get(eid, 0) + n
    return deg


def hub_threshold(deg: dict[int, int]) -> int:
    """Degree at or above which an entity is a hub: the 99th percentile, never below HUB_FLOOR."""
    if not deg:
        return HUB_FLOOR
    values = sorted(deg.values())
    return max(HUB_FLOOR, values[int(len(values) * 0.99) if len(values) > 1 else 0])


def _neighbors(db: sqlite3.Connection, entity_id: int, explicit_only: bool = False) -> list[sqlite3.Row]:
    sql = "SELECT b, relation, confidence, memory_id, explicit FROM entity_edges WHERE a = ?"
    if explicit_only:
        sql += " AND explicit = 1"
    return db.execute(sql, (entity_id,)).fetchall()


# ── Graph expansion for search ─────────────────────────────────────────────────

def expand(db: sqlite3.Connection, seed_memory_ids: list[int], query: str,
           depth: int = 1, limit: int = 25, kinds: Optional[list[str]] = None) -> list[int]:
    """
    Memories reached by walking the graph out from the seeds, best first.

    Start entities: those the top search results mention, plus any entity the question
    names. Walk `depth` hops. Hubs are never walked through, and a hub only contributes
    its memories when the question names it directly.
    """
    deg = degrees(db)
    if not deg:
        return []
    hub = hub_threshold(deg)
    named = set(entities_in_text(db, query))
    from_seeds: set[int] = set()
    for mid in seed_memory_ids:
        from_seeds.update(r[0] for r in db.execute("SELECT entity_id FROM mentions WHERE memory_id = ?", (mid,)))

    def is_hub(e: int) -> bool:
        return deg.get(e, 0) >= hub and e not in named

    hops: dict[int, int] = {e: 0 for e in (named | from_seeds) if not is_hub(e)}
    frontier = set(hops)
    for d in range(1, depth + 1):
        nxt: set[int] = set()
        for e in frontier:
            for row in _neighbors(db, e):
                b = row[0]
                if b not in hops and not is_hub(b):
                    hops[b] = d
                    nxt.add(b)
        frontier = nxt
    if not hops:
        return []

    marks = ", ".join("?" * len(hops))
    kind_sql = f" AND m.kind IN ({', '.join('?' * len(kinds))})" if kinds else ""
    rows = db.execute(
        f"""SELECT mn.memory_id, mn.entity_id, m.ts FROM mentions mn
            JOIN memory_items m ON m.id = mn.memory_id
            WHERE mn.entity_id IN ({marks}){kind_sql}""",
        [*hops, *(kinds or [])],
    ).fetchall()

    seeds = set(seed_memory_ids)
    score: dict[int, float] = {}
    newest: dict[int, float] = {}
    for mid, eid, ts in rows:
        if mid in seeds:
            continue
        # Closer entities count more; a memory touching several reached entities adds up.
        score[mid] = score.get(mid, 0.0) + 1.0 / (1 + hops[eid])
        newest[mid] = ts
    ranked = sorted(score, key=lambda m: (score[m], newest[m]), reverse=True)
    return ranked[:limit]


# ── Questions only a graph can answer ──────────────────────────────────────────

def about(db: sqlite3.Connection, name: str, limit: int = 8) -> Optional[dict]:
    """Everything the graph knows about one entity (Graphify's 'explain')."""
    ent = find_entity(db, name)
    if ent is None:
        return None
    eid = ent["id"]
    links = db.execute(
        """SELECT e.name AS other, r.relation, r.confidence, r.memory_id,
                  CASE WHEN r.src = ? THEN 'out' ELSE 'in' END AS direction
           FROM relations r JOIN entities e ON e.id = CASE WHEN r.src = ? THEN r.dst ELSE r.src END
           WHERE r.src = ? OR r.dst = ?
           ORDER BY r.confidence, r.ts DESC""",
        (eid, eid, eid, eid),
    ).fetchall()
    together = db.execute(
        """SELECT e.name, COUNT(*) AS n FROM mentions m1
           JOIN mentions m2 ON m2.memory_id = m1.memory_id AND m2.entity_id <> m1.entity_id
           JOIN entities e ON e.id = m2.entity_id
           WHERE m1.entity_id = ?
           GROUP BY e.id ORDER BY n DESC, e.name LIMIT ?""",
        (eid, limit),
    ).fetchall()
    memories = db.execute(
        """SELECT m.id, m.ts, m.kind, m.content, m.due_ts FROM mentions mn
           JOIN memory_items m ON m.id = mn.memory_id
           WHERE mn.entity_id = ? ORDER BY m.kind = 'exchange', m.ts DESC LIMIT ?""",
        (eid, limit),
    ).fetchall()
    total = db.execute("SELECT COUNT(*) FROM mentions WHERE entity_id = ?", (eid,)).fetchone()[0]
    return {"name": ent["name"], "links": [dict(r) for r in links], "mentioned_with": [dict(r) for r in together],
            "memories": [dict(r) for r in memories], "memory_count": total}


def connect(db: sqlite3.Connection, a: str, b: str, max_hops: int = 4) -> Optional[list[dict]]:
    """
    Shortest chain of links from a to b (Graphify's 'path'). Explicit relations are tried
    first; co-mentions are used only if no explicit chain exists. Hubs are never stepping
    stones, so the answer is never just "both appear near Sun".
    Returns [] when a and b are the same, None when no chain exists or a name is unknown.
    """
    ea, eb = find_entity(db, a), find_entity(db, b)
    if ea is None or eb is None:
        return None
    start, goal = ea["id"], eb["id"]
    if start == goal:
        return []
    deg = degrees(db)
    hub = hub_threshold(deg)
    names = dict(db.execute("SELECT id, name FROM entities").fetchall())

    for explicit_only in (True, False):
        parent: dict[int, tuple] = {start: None}
        queue = deque([(start, 0)])
        while queue:
            node, d = queue.popleft()
            if node == goal:
                steps = []
                while parent[node] is not None:
                    prev, rel, conf, mid = parent[node]
                    steps.append({"from": names[prev], "relation": rel, "to": names[node],
                                  "confidence": conf, "memory_id": mid})
                    node = prev
                return list(reversed(steps))
            if d >= max_hops or (node != start and deg.get(node, 0) >= hub):
                continue        # never step THROUGH a hub
            for row in _neighbors(db, node, explicit_only):
                nxt = row[0]
                if nxt not in parent:
                    parent[nxt] = (node, row[1], row[2], row[3])
                    queue.append((nxt, d + 1))
    return None


def stats(db: sqlite3.Connection) -> dict:
    return {
        "entities": db.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
        "relations": db.execute("SELECT COUNT(*) FROM relations").fetchone()[0],
        "relations_extracted": db.execute("SELECT COUNT(*) FROM relations WHERE confidence = 'EXTRACTED'").fetchone()[0],
        "mentions": db.execute("SELECT COUNT(*) FROM mentions").fetchone()[0],
    }
