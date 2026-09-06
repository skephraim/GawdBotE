"""
Shared test fixtures.

The tricky part: core/leads.py reads its database path once, at import time
(`_DB_PATH = Path(config.LEADS_DB)`). Setting an environment variable after
that module is already loaded changes nothing.

So each test that touches the database gets a fresh temp file AND a reload of
the three modules that captured the old value. `importlib.reload` re-runs a
module top to bottom, as if importing it for the first time.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

# Let tests import `config`, `core.leads`, … no matter where pytest is invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _reload_stack():
    """Reload config first, then everything that read values out of it."""
    import config
    import core.leads
    import tools.keisha

    importlib.reload(config)
    importlib.reload(core.leads)
    importlib.reload(tools.keisha)
    return config, core.leads, tools.keisha


@pytest.fixture
def keisha_env(tmp_path, monkeypatch):
    """
    A completely isolated Keisha: empty database, known settings.

    Pinning the settings matters as much as the empty database. A test that
    silently reads the developer's real .env passes or fails based on
    machine state, which makes it worse than no test at all.
    """
    monkeypatch.setenv("LEADS_DB", str(tmp_path / "test-leads.db"))
    monkeypatch.setenv("KEISHA_TARGET_NICHES", "plumbing,hvac,roofing,landscaping")
    monkeypatch.setenv("KEISHA_MIN_SCORE", "60")
    monkeypatch.setenv("KEISHA_DAILY_LIMIT", "50")
    monkeypatch.setenv("KEISHA_SEND_MODE", "dry_run")
    monkeypatch.setenv("KEISHA_REQUIRE_MISSED_CALLS", "false")
    monkeypatch.setenv("KEISHA_SENDER_NAME", "Test Sender")
    monkeypatch.setenv("KEISHA_BUSINESS_NAME", "Test Co LLC")
    monkeypatch.setenv("KEISHA_MAILING_ADDRESS", "1 Test St, Houston, TX")
    monkeypatch.setenv("KEISHA_CALENDAR_LINK", "")

    config, leads, keisha = _reload_stack()
    yield config, leads, keisha

    # Reload once more on the way out so the next test (and any test that does
    # NOT use this fixture) doesn't inherit our patched settings.
    monkeypatch.undo()
    _reload_stack()


@pytest.fixture
def csv_file(tmp_path):
    """Write a CSV and hand back its path."""
    def _write(name: str, content: str) -> str:
        p = tmp_path / name
        p.write_text(content, encoding="utf-8")
        return str(p)
    return _write
