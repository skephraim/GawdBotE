"""
Keisha's data layer — SQLite storage for leads, the approval outbox,
and the do-not-contact suppression list.

This module knows *nothing* about qualifying or writing pitches. Its only job
is to put rows in tables and get them back out again. Business rules live in
tools/keisha.py. Keeping storage separate from logic means you can change how
a lead is scored without ever touching the database, and vice-versa.

Same design as core/memory.py: plain SQLite, no external database server.
"""
from __future__ import annotations

import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

import config

_DB_PATH = Path(config.LEADS_DB)
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# ── Schema ────────────────────────────────────────────────────────────────────
# "IF NOT EXISTS" makes this safe to run on every connection: the first run
# creates the tables, every run after that is a no-op.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key    TEXT    NOT NULL UNIQUE,
    business_name TEXT    NOT NULL,
    contact_name  TEXT    NOT NULL DEFAULT '',
    email         TEXT    NOT NULL DEFAULT '',
    phone         TEXT    NOT NULL DEFAULT '',
    website       TEXT    NOT NULL DEFAULT '',
    niche         TEXT    NOT NULL DEFAULT '',
    city          TEXT    NOT NULL DEFAULT '',
    notes         TEXT    NOT NULL DEFAULT '',
    missed_calls  INTEGER NOT NULL DEFAULT 0,
    source        TEXT    NOT NULL DEFAULT 'manual',
    status        TEXT    NOT NULL DEFAULT 'new',
    score         INTEGER NOT NULL DEFAULT 0,
    tier          TEXT    NOT NULL DEFAULT '',
    reason        TEXT    NOT NULL DEFAULT '',
    created_ts    REAL    NOT NULL,
    updated_ts    REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id    INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    channel    TEXT    NOT NULL DEFAULT 'email',
    to_address TEXT    NOT NULL DEFAULT '',
    subject    TEXT    NOT NULL DEFAULT '',
    body       TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'pending',
    note       TEXT    NOT NULL DEFAULT '',
    created_ts REAL    NOT NULL,
    decided_ts REAL,
    sent_ts    REAL
);

CREATE TABLE IF NOT EXISTS suppression (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    value      TEXT NOT NULL UNIQUE,
    reason     TEXT NOT NULL DEFAULT '',
    created_ts REAL NOT NULL
);

"""

# Indexes live apart from the table DDL because they must be created *after*
# _migrate() has added any missing columns. An index on a column the table
# doesn't have yet fails outright — which is exactly what happens when an
# older database meets a newer index.
_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_leads_status  ON leads (status);
CREATE INDEX IF NOT EXISTS idx_leads_niche   ON leads (niche);
CREATE INDEX IF NOT EXISTS idx_leads_tier    ON leads (tier);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox (status);
CREATE INDEX IF NOT EXISTS idx_outbox_lead   ON outbox (lead_id);
"""

# Every value a lead's `status` column is allowed to hold.
LEAD_STATUSES = (
    "new", "qualified", "rejected", "queued",
    "contacted", "replied", "won", "lost", "do_not_contact",
)

# Every value an outbox row's `status` column is allowed to hold.
OUTBOX_STATUSES = ("pending", "approved", "rejected", "sent", "failed")


# Columns added after the first release. CREATE TABLE IF NOT EXISTS will not
# touch a table that already exists, so a database created before these columns
# existed needs them added by hand — that is what a migration is.
_MIGRATIONS = {
    "leads": {
        "tier": "TEXT NOT NULL DEFAULT ''",
        "missed_calls": "INTEGER NOT NULL DEFAULT 0",
    },
}


def _migrate(c: sqlite3.Connection) -> None:
    """Add any column listed in _MIGRATIONS that this database is missing."""
    for table, columns in _MIGRATIONS.items():
        # PRAGMA table_info asks SQLite to describe a table's own columns.
        have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, decl in columns.items():
            if name not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    """Open a connection, guarantee the schema, commit on success, always close."""
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row          # rows behave like dicts: row["email"]
    c.execute("PRAGMA foreign_keys = ON")  # off by default in SQLite
    try:
        c.executescript(_SCHEMA)    # 1. tables exist
        _migrate(c)                 # 2. old tables gain any new columns
        c.executescript(_INDEXES)   # 3. only now is it safe to index them
        yield c
        c.commit()
    finally:
        c.close()


def _row(r: Optional[sqlite3.Row]) -> Optional[dict]:
    return dict(r) if r is not None else None


def _rows(rs) -> list[dict]:
    return [dict(r) for r in rs]


# ── Dedupe ────────────────────────────────────────────────────────────────────

def dedupe_key(business_name: str, email: str = "", city: str = "") -> str:
    """
    Build the string that decides "is this the same lead I already have?"

    An email address is the strongest identity we have, so it wins when present.
    Otherwise we fall back to a squashed business name + city, which catches
    "Apex Plumbing" / "APEX  Plumbing, LLC." landing in the list twice.
    """
    if email.strip():
        return f"email:{email.strip().lower()}"
    name = re.sub(r"[^a-z0-9]+", "", business_name.lower())
    place = re.sub(r"[^a-z0-9]+", "", city.lower())
    return f"biz:{name}|{place}"


# ── Leads ─────────────────────────────────────────────────────────────────────

def upsert_lead(
    business_name: str,
    contact_name: str = "",
    email: str = "",
    phone: str = "",
    website: str = "",
    niche: str = "",
    city: str = "",
    notes: str = "",
    missed_calls: bool = False,
    source: str = "manual",
) -> tuple[int, bool]:
    """
    Insert a lead, or update the one already stored under the same dedupe key.

    Returns (lead_id, created) — `created` is True for a brand-new row, False
    when we merged into an existing one.
    """
    key = dedupe_key(business_name, email, city)
    now = time.time()
    with _conn() as db:
        existing = db.execute("SELECT id FROM leads WHERE dedupe_key = ?", (key,)).fetchone()
        if existing:
            # missed_calls uses MAX(old, new) so the flag is sticky: once you
            # learn a shop has a missed-call problem, a later import that
            # simply doesn't mention it cannot unlearn the fact.
            #
            # business_name is deliberately NOT updated: the first spelling we
            # stored is usually the clean one, and a later duplicate row like
            # "APEX  Plumbing LLC." should not overwrite "Apex Plumbing".
            #
            # COALESCE(NULLIF(?, ''), column) means: use the new value if it is
            # non-empty, otherwise keep whatever is already in the column. That
            # way a sparse re-import never wipes out details you already had.
            db.execute(
                """
                UPDATE leads SET
                    contact_name  = COALESCE(NULLIF(?, ''), contact_name),
                    email         = COALESCE(NULLIF(?, ''), email),
                    phone         = COALESCE(NULLIF(?, ''), phone),
                    website       = COALESCE(NULLIF(?, ''), website),
                    niche         = COALESCE(NULLIF(?, ''), niche),
                    city          = COALESCE(NULLIF(?, ''), city),
                    notes         = COALESCE(NULLIF(?, ''), notes),
                    missed_calls  = MAX(missed_calls, ?),
                    updated_ts    = ?
                WHERE id = ?
                """,
                (contact_name, email, phone, website,
                 niche, city, notes, int(missed_calls), now, existing["id"]),
            )
            return int(existing["id"]), False

        cur = db.execute(
            """
            INSERT INTO leads (dedupe_key, business_name, contact_name, email, phone,
                               website, niche, city, notes, missed_calls, source,
                               created_ts, updated_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (key, business_name, contact_name, email, phone, website,
             niche, city, notes, int(missed_calls), source, now, now),
        )
        return int(cur.lastrowid), True


def get_lead(lead_id: int) -> Optional[dict]:
    with _conn() as db:
        return _row(db.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone())


def find_leads(
    status: Optional[str] = None,
    niche: Optional[str] = None,
    tier: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """
    List leads, best first, optionally filtered by status, niche, and/or tier.

    Ordering is tier before score. Tiers sort as 'A' < 'B' alphabetically, and
    the CASE puts unqualified leads (empty tier) last instead of first, which
    is what a plain ORDER BY tier would do with an empty string.
    """
    sql = "SELECT * FROM leads WHERE 1 = 1"
    params: list = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if niche:
        sql += " AND niche = ?"
        params.append(niche)
    if tier:
        sql += " AND tier = ?"
        params.append(tier)
    sql += """
        ORDER BY CASE WHEN tier = '' THEN 1 ELSE 0 END, tier ASC,
                 score DESC, created_ts DESC
        LIMIT ?
    """
    params.append(limit)
    with _conn() as db:
        return _rows(db.execute(sql, params).fetchall())


def set_lead_status(lead_id: int, status: str, score: Optional[int] = None,
                    reason: str = "", tier: Optional[str] = None) -> None:
    if status not in LEAD_STATUSES:
        raise ValueError(f"Unknown lead status {status!r}; expected one of {LEAD_STATUSES}")
    with _conn() as db:
        db.execute(
            """
            UPDATE leads SET
                status     = ?,
                score      = COALESCE(?, score),
                tier       = COALESCE(?, tier),
                reason     = COALESCE(NULLIF(?, ''), reason),
                updated_ts = ?
            WHERE id = ?
            """,
            (status, score, tier, reason, time.time(), lead_id),
        )


# ── Outbox (the human-approval queue) ─────────────────────────────────────────

def queue_message(lead_id: int, channel: str, to_address: str,
                  subject: str, body: str) -> int:
    """Park a drafted message in the outbox as `pending`. Nothing is sent here."""
    with _conn() as db:
        cur = db.execute(
            """
            INSERT INTO outbox (lead_id, channel, to_address, subject, body, status, created_ts)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (lead_id, channel, to_address, subject, body, time.time()),
        )
        return int(cur.lastrowid)


def get_outbox_item(outbox_id: int) -> Optional[dict]:
    with _conn() as db:
        return _row(db.execute("SELECT * FROM outbox WHERE id = ?", (outbox_id,)).fetchone())


def list_outbox(status: Optional[str] = "pending", limit: int = 50) -> list[dict]:
    """
    List outbox rows joined to their lead, so the review screen can show who
    each message is going to without a second query.
    """
    sql = """
        SELECT o.*, l.business_name, l.contact_name, l.niche, l.city
        FROM outbox o
        JOIN leads l ON l.id = o.lead_id
    """
    params: list = []
    if status:
        sql += " WHERE o.status = ?"
        params.append(status)
    sql += " ORDER BY o.created_ts ASC LIMIT ?"
    params.append(limit)
    with _conn() as db:
        return _rows(db.execute(sql, params).fetchall())


def set_outbox_status(outbox_id: int, status: str, note: str = "") -> None:
    if status not in OUTBOX_STATUSES:
        raise ValueError(f"Unknown outbox status {status!r}; expected one of {OUTBOX_STATUSES}")
    now = time.time()
    decided = now if status in ("approved", "rejected") else None
    sent = now if status == "sent" else None
    with _conn() as db:
        db.execute(
            """
            UPDATE outbox SET
                status     = ?,
                note       = COALESCE(NULLIF(?, ''), note),
                decided_ts = COALESCE(?, decided_ts),
                sent_ts    = COALESCE(?, sent_ts)
            WHERE id = ?
            """,
            (status, note, decided, sent, outbox_id),
        )


def has_open_message(lead_id: int) -> bool:
    """True if this lead already has a message pending, approved, or sent."""
    with _conn() as db:
        row = db.execute(
            """
            SELECT 1 FROM outbox
            WHERE lead_id = ? AND status IN ('pending', 'approved', 'sent')
            LIMIT 1
            """,
            (lead_id,),
        ).fetchone()
    return row is not None


def sent_since(since_ts: float) -> int:
    """
    How many messages have actually been sent since `since_ts`.

    This is the real daily counter. Reading it from the database rather than an
    in-memory variable means restarting the bot cannot reset your limit back
    to zero and let it blow through the cap a second time.
    """
    with _conn() as db:
        row = db.execute(
            "SELECT COUNT(*) AS n FROM outbox WHERE status = 'sent' AND sent_ts >= ?",
            (since_ts,),
        ).fetchone()
    return int(row["n"])


# ── Suppression / do-not-contact ──────────────────────────────────────────────

def suppress(value: str, reason: str = "") -> str:
    """
    Add an email address or a whole domain (e.g. "acme.com") to the block list.
    Anyone who asks to be left alone goes here, permanently.
    """
    v = value.strip().lower().lstrip("@")
    if not v:
        return "Nothing to suppress (empty value)."
    with _conn() as db:
        db.execute(
            "INSERT OR IGNORE INTO suppression (value, reason, created_ts) VALUES (?, ?, ?)",
            (v, reason, time.time()),
        )
    return f"Suppressed {v!r} — Keisha will never contact it again."


def is_suppressed(email: str) -> bool:
    """True if the address itself, or its domain, is on the block list."""
    e = email.strip().lower()
    if not e:
        return False
    domain = e.split("@")[-1]
    with _conn() as db:
        row = db.execute(
            "SELECT 1 FROM suppression WHERE value = ? OR value = ? LIMIT 1",
            (e, domain),
        ).fetchone()
    return row is not None


def list_suppressed(limit: int = 100) -> list[dict]:
    with _conn() as db:
        return _rows(db.execute(
            "SELECT * FROM suppression ORDER BY created_ts DESC LIMIT ?", (limit,)
        ).fetchall())


# ── Reporting ─────────────────────────────────────────────────────────────────

def stats() -> dict:
    """Counts by lead status and by outbox status, for the pipeline dashboard."""
    with _conn() as db:
        # GROUP BY turns many rows into one row per distinct status,
        # and COUNT(*) tells you how many rows fell into each group.
        leads_by_status = {
            r["status"]: r["n"]
            for r in db.execute(
                "SELECT status, COUNT(*) AS n FROM leads GROUP BY status"
            ).fetchall()
        }
        outbox_by_status = {
            r["status"]: r["n"]
            for r in db.execute(
                "SELECT status, COUNT(*) AS n FROM outbox GROUP BY status"
            ).fetchall()
        }
        # Only count tiers for leads still worth working — a tier on a lead
        # you already contacted is history, not a to-do.
        by_tier = {
            r["tier"]: r["n"]
            for r in db.execute(
                """
                SELECT tier, COUNT(*) AS n FROM leads
                WHERE tier != '' AND status IN ('qualified', 'queued')
                GROUP BY tier
                """
            ).fetchall()
        }
        total_leads = db.execute("SELECT COUNT(*) AS n FROM leads").fetchone()["n"]
        suppressed = db.execute("SELECT COUNT(*) AS n FROM suppression").fetchone()["n"]
    return {
        "total_leads": total_leads,
        "leads_by_status": leads_by_status,
        "open_by_tier": by_tier,
        "outbox_by_status": outbox_by_status,
        "suppressed": suppressed,
    }
