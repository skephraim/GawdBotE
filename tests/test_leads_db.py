"""
Storage-layer tests: dedupe, sticky flags, and schema migration.

Every test here is a regression test for a bug that actually happened during
development. That's the honest reason to write a test — not coverage for its
own sake, but "this broke once and must never break silently again."
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


# ── Dedupe ────────────────────────────────────────────────────────────────────

def test_same_email_merges_into_one_lead(keisha_env):
    _, leads, _ = keisha_env
    id1, created1 = leads.upsert_lead("Apex Plumbing", email="john@apex.com", niche="plumbing")
    id2, created2 = leads.upsert_lead("APEX  Plumbing, LLC.", email="john@apex.com", niche="plumbing")

    assert created1 is True
    assert created2 is False, "same email must merge, not create a second lead"
    assert id1 == id2


def test_merge_keeps_the_original_business_name(keisha_env):
    """The first spelling we stored is usually the clean one."""
    _, leads, _ = keisha_env
    lead_id, _ = leads.upsert_lead("Apex Plumbing", email="john@apex.com")
    leads.upsert_lead("APEX  Plumbing, LLC.", email="john@apex.com")

    assert leads.get_lead(lead_id)["business_name"] == "Apex Plumbing"


def test_merge_fills_blanks_without_erasing_known_values(keisha_env):
    """COALESCE(NULLIF(?, ''), column): a sparse re-import must not wipe data."""
    _, leads, _ = keisha_env
    lead_id, _ = leads.upsert_lead(
        "Apex Plumbing", email="john@apex.com", phone="713-555-0182", city="Houston")
    leads.upsert_lead("Apex Plumbing", email="john@apex.com", niche="plumbing")

    lead = leads.get_lead(lead_id)
    assert lead["phone"] == "713-555-0182", "existing phone was erased by a blank"
    assert lead["city"] == "Houston"
    assert lead["niche"] == "plumbing", "new value should still fill an empty column"


def test_leads_without_email_dedupe_on_name_and_city(keisha_env):
    _, leads, _ = keisha_env
    id1, _ = leads.upsert_lead("Nolan Landscaping", city="Pearland", phone="832-555-0144")
    id2, created = leads.upsert_lead("nolan  landscaping", city="pearland")
    assert created is False and id1 == id2

    id3, created3 = leads.upsert_lead("Nolan Landscaping", city="Katy")
    assert created3 is True, "same name in a different city is a different business"
    assert id3 != id1


# ── Sticky missed-call flag ───────────────────────────────────────────────────

def test_missed_calls_is_stored_not_forgotten(keisha_env):
    """
    Regression: missed_calls used to be a transient dict key, never a column,
    so it vanished the moment a lead was saved.
    """
    _, leads, _ = keisha_env
    lead_id, _ = leads.upsert_lead("Apex Plumbing", email="j@a.com", missed_calls=True)
    assert leads.get_lead(lead_id)["missed_calls"] == 1


def test_missed_calls_flag_is_sticky(keisha_env):
    """MAX(old, new): true once, true forever. A lazier re-import can't unlearn it."""
    _, leads, _ = keisha_env
    lead_id, _ = leads.upsert_lead("Apex Plumbing", email="j@a.com", missed_calls=True)
    leads.upsert_lead("Apex Plumbing", email="j@a.com", missed_calls=False)

    assert leads.get_lead(lead_id)["missed_calls"] == 1, "sticky flag was cleared"


# ── Migration ─────────────────────────────────────────────────────────────────

# The leads table exactly as it existed before `tier` and `missed_calls` were
# added. Written out in full so this test keeps meaning even as the live
# schema moves on.
_OLD_SCHEMA = """
CREATE TABLE leads (
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
    source        TEXT    NOT NULL DEFAULT 'manual',
    status        TEXT    NOT NULL DEFAULT 'new',
    score         INTEGER NOT NULL DEFAULT 0,
    reason        TEXT    NOT NULL DEFAULT '',
    created_ts    REAL    NOT NULL,
    updated_ts    REAL    NOT NULL
);
"""


def test_old_database_gains_new_columns_with_rows_intact(keisha_env):
    """
    Regression: opening a pre-tier database raised
    `sqlite3.OperationalError: no such column: tier`, because the new index
    was created before the migration that adds the column.
    """
    _, leads, _ = keisha_env
    db_path = Path(leads._DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Build a database the way the previous release would have.
    old = sqlite3.connect(db_path)
    old.executescript(_OLD_SCHEMA)
    now = time.time()
    old.execute(
        "INSERT INTO leads (dedupe_key, business_name, email, niche, status, score,"
        " created_ts, updated_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("email:john@apex.com", "Apex Plumbing", "john@apex.com", "plumbing",
         "qualified", 85, now, now),
    )
    old.commit()
    old.close()

    # Simply reading through the current code must migrate it, not crash.
    rows = leads.find_leads(limit=10)

    assert len(rows) == 1, "the pre-existing row did not survive the migration"
    assert rows[0]["business_name"] == "Apex Plumbing"
    assert rows[0]["score"] == 85, "existing data was clobbered"
    assert rows[0]["tier"] == ""       # new column, default value
    assert rows[0]["missed_calls"] == 0


def test_migration_is_idempotent(keisha_env):
    """Running it twice must not fail with 'duplicate column name'."""
    _, leads, _ = keisha_env
    leads.upsert_lead("Apex Plumbing", email="j@a.com")
    leads.find_leads()
    leads.find_leads()   # second open, migration runs again

    with sqlite3.connect(leads._DB_PATH) as c:
        cols = [r[1] for r in c.execute("PRAGMA table_info(leads)")]
    assert cols.count("tier") == 1
    assert cols.count("missed_calls") == 1


# ── Suppression ───────────────────────────────────────────────────────────────

def test_suppression_matches_address_and_whole_domain(keisha_env):
    _, leads, _ = keisha_env
    leads.suppress("john@apex.com")
    leads.suppress("spamtrap.io")

    assert leads.is_suppressed("john@apex.com") is True
    assert leads.is_suppressed("JOHN@APEX.COM") is True, "matching must be case-insensitive"
    assert leads.is_suppressed("anyone@spamtrap.io") is True, "domain block must cover the domain"
    assert leads.is_suppressed("someone@else.com") is False
    assert leads.is_suppressed("") is False


def test_suppress_is_idempotent(keisha_env):
    _, leads, _ = keisha_env
    leads.suppress("john@apex.com", "asked to stop")
    leads.suppress("john@apex.com", "asked again")
    assert len(leads.list_suppressed()) == 1


# ── Ordering ──────────────────────────────────────────────────────────────────

def test_find_leads_puts_tier_a_first_and_unqualified_last(keisha_env):
    """A plain ORDER BY tier would sort the empty strings to the TOP."""
    _, leads, _ = keisha_env
    b_id, _ = leads.upsert_lead("B Shop", email="b@x.com")
    a_id, _ = leads.upsert_lead("A Shop", email="a@x.com")
    none_id, _ = leads.upsert_lead("No Tier", email="n@x.com")

    leads.set_lead_status(b_id, "qualified", score=85, tier="B")
    leads.set_lead_status(a_id, "qualified", score=100, tier="A")
    leads.set_lead_status(none_id, "rejected", score=10, tier="")

    assert [r["id"] for r in leads.find_leads(limit=10)] == [a_id, b_id, none_id]


def test_find_leads_filters_by_tier(keisha_env):
    _, leads, _ = keisha_env
    a_id, _ = leads.upsert_lead("A Shop", email="a@x.com")
    b_id, _ = leads.upsert_lead("B Shop", email="b@x.com")
    leads.set_lead_status(a_id, "qualified", score=100, tier="A")
    leads.set_lead_status(b_id, "qualified", score=85, tier="B")

    assert [r["id"] for r in leads.find_leads(tier="A")] == [a_id]


def test_set_lead_status_rejects_an_unknown_status(keisha_env):
    import pytest
    _, leads, _ = keisha_env
    lead_id, _ = leads.upsert_lead("Apex Plumbing", email="j@a.com")
    with pytest.raises(ValueError):
        leads.set_lead_status(lead_id, "totally_made_up")
