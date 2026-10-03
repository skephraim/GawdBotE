"""
Event log: the agent's chart. Append only, stored in SQLite on your own disk.

Every run writes rows like:
  run_start    what was asked, from which channel
  llm_reply    which brain answered, what it said, which tools it asked for
  tool_call    tool name, risk tier, the arguments (secrets masked)
  approval     requested, approved, denied, timed out, or trusted, and by whom
  tool_result  ok, error or blocked, how long it took, a preview of the output
  run_end      final answer preview, number of rounds, status

View it with:
  python main.py log               the most recent runs
  python main.py log <session_id>  the full step by step trace of one run
  /trace in Telegram, !trace in Discord, /trace in the CLI chat

Golden rule: logging must NEVER break the agent. Every write is wrapped so a full disk
or a locked file costs you a log line, not your answer.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import config

log = logging.getLogger(__name__)

MAX_FIELD_CHARS = 2_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    session_id  TEXT    NOT NULL,
    channel     TEXT    NOT NULL,
    kind        TEXT    NOT NULL,
    tool        TEXT,
    status      TEXT,
    duration_ms INTEGER,
    detail      TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, id);
CREATE INDEX IF NOT EXISTS idx_events_ts      ON events(ts);
"""

_SECRET_KEY_RE = re.compile(r"(key|token|secret|password|passwd|credential|auth)", re.I)


def _db_path() -> Path:
    return Path(config.EVENTS_DB)


def _conn() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=5)
    c.execute("PRAGMA journal_mode=WAL")   # readers never block the writer
    c.executescript(_SCHEMA)
    c.row_factory = sqlite3.Row
    return c


# ── Cleaning data before it is written ─────────────────────────────────────────

def _shorten(text: str, limit: int = MAX_FIELD_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + f" [... {len(text) - limit:,} more chars]"


def redact(value: Any, limit: int = MAX_FIELD_CHARS) -> Any:
    """Mask values whose key looks secret, and shorten long strings, all the way down."""
    if isinstance(value, dict):
        return {k: ("***" if _SECRET_KEY_RE.search(str(k)) else redact(v, limit)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, limit) for v in value]
    if isinstance(value, str):
        return _shorten(value, limit)
    return value


# ── Writing ────────────────────────────────────────────────────────────────────

def record(
    ctx,
    kind: str,
    *,
    tool: Optional[str] = None,
    status: Optional[str] = None,
    duration_ms: Optional[int] = None,
    **detail: Any,
) -> None:
    """Append one event. Never raises."""
    try:
        with _conn() as db:
            db.execute(
                "INSERT INTO events (ts, session_id, channel, kind, tool, status, duration_ms, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    time.time(), ctx.session_id, ctx.channel, kind, tool, status, duration_ms,
                    json.dumps(redact(detail), default=str),
                ),
            )
    except Exception as exc:   # the chart is down; the patient still gets treated
        log.warning("Event log write failed (%s): %s", kind, exc)


# ── Reading ────────────────────────────────────────────────────────────────────

def session_events(session_id: str) -> list[dict]:
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM events WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
    return [dict(r) | {"detail": json.loads(r["detail"])} for r in rows]


def recent_sessions(limit: int = 10, channel: Optional[str] = None) -> list[dict]:
    """One summary row per run, newest first."""
    sql = """
        SELECT session_id,
               channel,
               MIN(ts)                                          AS started,
               MAX(ts)                                          AS ended,
               SUM(CASE WHEN kind = 'tool_call' THEN 1 ELSE 0 END) AS tool_calls,
               SUM(CASE WHEN kind = 'approval' AND status IN ('denied', 'timeout', 'unavailable')
                        THEN 1 ELSE 0 END)                      AS refused,
               MAX(CASE WHEN kind = 'run_end' THEN status END)  AS outcome
        FROM events
        WHERE (? IS NULL OR channel = ?)
        GROUP BY session_id, channel
        ORDER BY ended DESC
        LIMIT ?
    """
    with _conn() as db:
        rows = db.execute(sql, (channel, channel, limit)).fetchall()
    return [dict(r) for r in rows]


def last_session_id(channel: Optional[str] = None) -> Optional[str]:
    rows = recent_sessions(1, channel)
    return rows[0]["session_id"] if rows else None


def prune(older_than_days: int) -> int:
    """Delete events older than N days. Returns how many rows were removed."""
    cutoff = time.time() - older_than_days * 86_400
    with _conn() as db:
        return db.execute("DELETE FROM events WHERE ts < ?", (cutoff,)).rowcount


# ── Formatting for people ──────────────────────────────────────────────────────

def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _flat(text: str, limit: int) -> str:
    """One line, shortened: multi line tool output would otherwise break the trace layout."""
    return _shorten(" ".join(str(text).split()), limit)


def format_session(session_id: str, max_chars: int = 3_500) -> str:
    """A readable step by step trace of one run, sized to fit in a chat message."""
    rows = session_events(session_id)
    if not rows:
        return f"No events found for run {session_id}."

    lines = [f"Run {session_id} on {rows[0]['channel']}"]
    for e in rows:
        d, t = e["detail"], _clock(e["ts"])
        k = e["kind"]
        if k == "run_start":
            lines.append(f"{t} asked: {_flat(d.get('message', ''), 200)}")
        elif k == "llm_reply":
            wants = ", ".join(d.get("tools", [])) or "no tools"
            said = _flat(d.get("text", ""), 160)
            lines.append(f"{t} brain ({d.get('provider', '?')}) round {d.get('round')}: wants {wants}" + (f". Said: {said}" if said else ""))
        elif k == "tool_call":
            lines.append(f"{t} call {e['tool']} [{d.get('risk')}] {_flat(json.dumps(d.get('args', {})), 200)}")
        elif k == "approval":
            who = f" by {d['by']}" if d.get("by") else ""
            lines.append(f"{t} approval {e['tool']}: {e['status']}{who}")
        elif k == "tool_result":
            ms = f" in {e['duration_ms']}ms" if e["duration_ms"] is not None else ""
            lines.append(f"{t} result {e['tool']}: {e['status']}{ms}. {_flat(d.get('preview', ''), 160)}")
        elif k == "run_end":
            lines.append(f"{t} done ({e['status']}, {d.get('rounds')} rounds): {_flat(d.get('final', ''), 200)}")
        else:
            lines.append(f"{t} {k} {e['status'] or ''}")

    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[: max_chars - 40] + "\n[... trace cut off, use the CLI]"


def format_recent(limit: int = 10, channel: Optional[str] = None) -> str:
    rows = recent_sessions(limit, channel)
    if not rows:
        return "No runs logged yet."
    out = ["session       started           channel   activity"]
    for r in rows:
        when = datetime.fromtimestamp(r["started"]).strftime("%Y-%m-%d %H:%M")
        out.append(
            f"{r['session_id']}  {when}  {r['channel']:<9} tools:{r['tool_calls']:<3} "
            f"refused:{r['refused']:<2} {r['outcome'] or 'running'}"
        )
    return "\n".join(out)
