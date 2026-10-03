"""Shared test setup: every test gets its own throwaway event log and a clean approval state."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.setattr(config, "TRUSTED_TOOLS", set())
    from core import approvals
    approvals.clear_owner_channel()
    approvals._pending.clear()
    yield
    approvals.clear_owner_channel()
    approvals._pending.clear()
