"""Shared test setup: every test gets its own throwaway event log and a clean approval state."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from core import llm as _llm  # noqa: E402

# The real embed function, saved before the autouse fixture swaps in an offline stand in.
REAL_EMBED = _llm.embed


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.setattr(config, "TRUSTED_TOOLS", set())
    monkeypatch.setattr(config, "MEMORY_DB", str(tmp_path / "memory.db"))
    monkeypatch.setattr(config, "BRAINS_FILE", str(tmp_path / "brains.toml"))      # absent unless a test writes it
    monkeypatch.setattr(config, "BRAIN_STATE_FILE", str(tmp_path / "brain_state.json"))
    # No real network in tests: by default there is no embedding brain. Tests that need
    # vectors install a fake embedder.
    from core import llm

    async def _no_embedder(texts, purpose="passage"):
        return None
    monkeypatch.setattr(llm, "embed", _no_embedder)
    llm._down_until.clear()           # every test starts with every brain healthy
    from core import approvals
    approvals.clear_owner_channel()
    approvals._pending.clear()
    yield
    approvals.clear_owner_channel()
    approvals._pending.clear()
