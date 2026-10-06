"""
Phase 2 tests: brains (config, fallback, live swap, usage and cost), memory (hybrid search,
kinds, migration, recall), and reporting spend to Paperclip.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

import config
from core import agent, brains, events, llm, memory, registry
from core.context import RunContext


def run(coro):
    return asyncio.run(coro)


# ── Helpers ────────────────────────────────────────────────────────────────────

def write_brains(text: str) -> None:
    path = config.BRAINS_FILE
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    later = time.time() + 5 + len(text) / 1000       # force a new modification time so the cache reloads
    os.utime(path, (later, later))


GOOD = """
[chains]
main = ["primary", "backup"]
vision = ["primary"]
embedding = ["embedder"]

[brains.primary]
kind = "anthropic"
model = "claude-sonnet-5-5"
api_key = "ANTHROPIC_API_KEY"
vision = true
price_in = 3.0
price_out = 15.0

[brains.backup]
kind = "openai_compat"
base_url = "https://openrouter.ai/api/v1"
model = "some/model"
api_key = "OPENROUTER_API_KEY"

[brains.local]
kind = "openai_compat"
base_url = "http://localhost:11434/v1"
model = "llama3.3"
no_key = true

[brains.embedder]
kind = "openai_compat"
base_url = "https://api.openai.com/v1"
model = "text-embedding-3-small"
api_key = "OPENAI_API_KEY"
tools = false
"""


@pytest.fixture
def keys(monkeypatch):
    """Pretend these secrets are in the vault."""
    store = {"ANTHROPIC_API_KEY": "a-key", "OPENROUTER_API_KEY": "o-key", "OPENAI_API_KEY": "e-key"}
    monkeypatch.setattr(config, "_secret", lambda name, default="": store.get(name, default))
    return store


# Concept based fake embedder: words map to "ideas", so meaning search can find text that
# shares no words with the question. Real embedders do this with billions of parameters.
CONCEPTS = [
    {"music", "song", "track", "single", "album", "mix"},
    {"money", "budget", "cost", "price", "spend", "dollars", "expensive"},
    {"meeting", "call", "zoom", "sync"},
]


def concept_vector(text: str) -> list[float]:
    words = set(text.lower().replace("?", " ").split())
    return [float(len(words & c)) for c in CONCEPTS] + [0.01]


def fake_embedder(model="fake:concepts-v1", vectorize=concept_vector, calls=None):
    async def embed(texts, purpose="passage"):
        if calls is not None:
            calls.append((list(texts), purpose))
        return [vectorize(t) for t in texts], model
    return embed


# ── Brains: loading brains.toml ────────────────────────────────────────────────

def test_brains_toml_is_loaded_with_chains(keys):
    write_brains(GOOD)
    setup = brains.load()
    assert setup.source == "brains.toml"
    assert [b.name for b in brains.chain("main")] == ["primary", "backup"]
    assert [b.name for b in brains.chain("embedding")] == ["embedder"]


def test_missing_main_chain_defaults_to_tool_capable_brains(keys):
    write_brains(GOOD.replace('main = ["primary", "backup"]\n', ""))
    assert [b.name for b in brains.chain("main")] == ["primary", "backup", "local"]


@pytest.mark.parametrize("bad, complaint", [
    ('api_key = "ANTHROPIC_API_KEY"', 'api_key = "sk-ant-REALKEY123456"'),
    ('model = "claude-sonnet-5-5"', 'modle = "claude-sonnet-5-5"\nmodel = "x"'),
    ('kind = "anthropic"', 'kind = "skynet"'),
    ('base_url = "https://openrouter.ai/api/v1"\n', ""),
    ('main = ["primary", "backup"]', 'main = ["primary", "ghost"]'),
])
def test_bad_brain_configs_are_rejected_clearly(keys, bad, complaint):
    write_brains(GOOD.replace(bad, complaint, 1))
    with pytest.raises(brains.BrainConfigError):
        brains.load()


def test_real_key_pasted_into_config_is_refused_with_advice(keys):
    write_brains(GOOD.replace('api_key = "OPENROUTER_API_KEY"', 'api_key = "sk-or-v1-abcdef0123456789"'))
    with pytest.raises(brains.BrainConfigError, match="NAME of a secret"):
        brains.load()


def test_brain_without_tools_cannot_be_in_main_chain(keys):
    write_brains(GOOD.replace('main = ["primary", "backup"]', 'main = ["primary", "embedder"]'))
    with pytest.raises(brains.BrainConfigError, match="tool calling"):
        brains.load()


def test_anthropic_cannot_be_an_embedder(keys):
    write_brains(GOOD.replace('embedding = ["embedder"]', 'embedding = ["primary"]'))
    with pytest.raises(brains.BrainConfigError, match="no embeddings"):
        brains.load()


def test_broken_toml_gives_a_readable_error(keys):
    write_brains("[brains.x\nkind = ")
    with pytest.raises(brains.BrainConfigError, match="not valid TOML"):
        brains.load()


def test_config_edits_apply_without_restart(keys):
    write_brains(GOOD)
    assert brains.chain("main")[0].name == "primary"
    write_brains(GOOD.replace('main = ["primary", "backup"]', 'main = ["backup", "primary"]'))
    assert brains.chain("main")[0].name == "backup"


def test_without_brains_toml_the_old_env_settings_still_work(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDERS", ["anthropic", "ollama"])
    setup = brains.load()
    assert setup.source == "environment (.env)"
    assert setup.chains["main"] == ["anthropic", "ollama"]
    assert setup.chains["vision"] == ["anthropic", "ollama-vision"]
    assert setup.chains["embedding"] == ["ollama-embed"]


# ── Brains: live swap ──────────────────────────────────────────────────────────

def test_use_moves_a_brain_to_the_front_and_survives_restart(keys):
    write_brains(GOOD)
    assert "Main brain is now local" in brains.use("local")
    assert [b.name for b in brains.chain("main")] == ["local", "primary", "backup"]
    with open(config.BRAIN_STATE_FILE) as f:
        assert json.load(f)["main"] == "local"          # persisted, so a restart keeps it
    brains.reset()
    assert brains.chain("main")[0].name == "primary"


def test_use_refuses_unknown_and_tool_less_brains(keys):
    write_brains(GOOD)
    assert "No brain called" in brains.use("ghost")
    assert "cannot call tools" in brains.use("embedder")
    assert brains.chain("main")[0].name == "primary"


def test_use_warns_when_the_key_is_missing(keys):
    write_brains(GOOD)
    keys.pop("OPENROUTER_API_KEY")
    assert "Warning" in brains.use("backup")


def test_describe_and_command_parsing(keys):
    write_brains(GOOD)
    keys.pop("OPENROUTER_API_KEY")
    text = brains.handle_command([])
    assert "primary (claude-sonnet-5-5) [ready]" in text
    assert "backup (some/model) [skipped" in text
    assert "price unknown" in text
    assert "Usage" in brains.handle_command(["dance"])


def test_brain_cost_and_provider_labels():
    b = brains.Brain("x", "openai_compat", "m", "https://openrouter.ai/api/v1", price_in=3.0, price_out=15.0)
    assert b.cost_usd(1_000_000, 100_000) == pytest.approx(4.5)
    assert brains.Brain("y", "openai_compat", "m", "http://localhost:11434/v1").cost_usd(10, 10) is None
    assert b.provider == "openrouter"
    assert brains.Brain("z", "openai_compat", "m", "http://127.0.0.1:1234/v1").provider == "local"
    assert brains.Brain("a", "anthropic", "m").provider == "anthropic"


# ── LLM: fallback, usage, adapters ─────────────────────────────────────────────

def test_fallback_to_next_brain_with_reasons_and_cost(keys, monkeypatch):
    write_brains(GOOD)
    tried = []

    async def fake_call(brain, messages, tools, system):
        tried.append(brain.name)
        if brain.name == "primary":
            raise ConnectionError("anthropic is down")
        return {"content": "hi", "tool_calls": [], "finish_reason": "stop",
                "usage": {"input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 0}}

    monkeypatch.setattr(llm, "_call", fake_call)
    resp = run(llm.chat([{"role": "user", "content": "x"}], tools=[{"type": "function"}]))
    assert tried == ["primary", "backup"]
    assert resp["brain"] == "backup" and resp["provider_name"] == "openrouter"
    assert "anthropic is down" in resp["fallbacks"][0]
    assert resp["cost_usd"] is None                       # backup has no price set


def test_brains_without_keys_are_skipped_without_calling(keys, monkeypatch):
    write_brains(GOOD)
    keys.pop("ANTHROPIC_API_KEY")
    tried = []

    async def fake_call(brain, *a):
        tried.append(brain.name)
        return {"content": "ok", "tool_calls": [], "finish_reason": "stop",
                "usage": {"input_tokens": 1, "output_tokens": 1, "cached_input_tokens": 0}}

    monkeypatch.setattr(llm, "_call", fake_call)
    resp = run(llm.chat([{"role": "user", "content": "x"}]))
    assert tried == ["backup"] and "not in the vault" in resp["fallbacks"][0]


def test_every_brain_failing_lists_every_reason(keys, monkeypatch):
    write_brains(GOOD)

    async def fail(brain, *a):
        raise TimeoutError(f"{brain.name} timed out")

    monkeypatch.setattr(llm, "_call", fail)
    with pytest.raises(RuntimeError) as err:
        run(llm.chat([{"role": "user", "content": "x"}]))
    assert "primary timed out" in str(err.value) and "backup timed out" in str(err.value)


def test_priced_brain_reports_cost(keys, monkeypatch):
    write_brains(GOOD)

    async def ok(brain, *a):
        return {"content": "ok", "tool_calls": [], "finish_reason": "stop",
                "usage": {"input_tokens": 1_000_000, "output_tokens": 0, "cached_input_tokens": 0}}

    monkeypatch.setattr(llm, "_call", ok)
    assert run(llm.chat([{"role": "user", "content": "x"}]))["cost_usd"] == pytest.approx(3.0)


def test_openai_adapter_normalizes_usage_and_adds_system(monkeypatch):
    sent = {}

    class Completions:
        async def create(self, **params):
            sent.update(params)
            msg = SimpleNamespace(content="hello", tool_calls=[
                SimpleNamespace(id="t1", function=SimpleNamespace(name="git_status", arguments="{}"))])
            return SimpleNamespace(
                choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")],
                usage=SimpleNamespace(prompt_tokens=50, completion_tokens=7,
                                      prompt_tokens_details=SimpleNamespace(cached_tokens=10)))

    monkeypatch.setattr(llm, "_openai_client", lambda b, **k: SimpleNamespace(chat=SimpleNamespace(completions=Completions())))
    b = brains.Brain("x", "openai_compat", "model-x", "http://localhost:1/v1", no_key=True)
    out = run(llm._call_openai_compat(b, [{"role": "user", "content": "hi"}], tools=[{"t": 1}], system="be brief"))
    assert sent["messages"][0] == {"role": "system", "content": "be brief"}
    assert out["usage"] == {"input_tokens": 50, "output_tokens": 7, "cached_input_tokens": 10}
    assert out["tool_calls"] == [{"id": "t1", "name": "git_status", "arguments": "{}"}]


def test_anthropic_adapter_reads_usage(monkeypatch):
    class Messages:
        async def create(self, **params):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="yo")], stop_reason="end_turn",
                                   usage=SimpleNamespace(input_tokens=12, output_tokens=3, cache_read_input_tokens=4))

    monkeypatch.setattr(llm, "_anthropic_client", lambda b: SimpleNamespace(messages=Messages()))
    out = run(llm._call_anthropic([{"role": "user", "content": "x"}], brain=brains.Brain("a", "anthropic", "m")))
    assert out["usage"] == {"input_tokens": 12, "output_tokens": 3, "cached_input_tokens": 4}


def test_vision_uses_the_right_image_format_per_provider(keys, monkeypatch):
    write_brains(GOOD.replace('vision = ["primary"]', 'vision = ["primary", "local"]'))
    seen = []

    async def ant(messages, brain=None, **k):
        seen.append(("anthropic", messages[0]["content"][0]))
        raise ConnectionError("down")

    async def oai(brain, messages, tools=None, system=""):
        seen.append(("openai", messages[0]["content"][0]))
        return {"content": "a login screen"}

    monkeypatch.setattr(llm, "_call_anthropic", ant)
    monkeypatch.setattr(llm, "_call_openai_compat", oai)
    assert run(llm.describe_image("data:image/png;base64,QUJD", "what is this")) == "a login screen"
    assert seen[0][1] == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}
    assert seen[1][1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}


def test_real_embed_passes_input_type_only_when_asked(keys, monkeypatch):
    write_brains(GOOD)
    calls = []

    class Embeddings:
        async def create(self, **params):
            calls.append(params)
            return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2])])

    monkeypatch.setattr(llm, "_openai_client", lambda b, **k: SimpleNamespace(embeddings=Embeddings()))
    from conftest import REAL_EMBED            # conftest swapped llm.embed for an offline stand in
    vectors, model = run(REAL_EMBED(["hello"], "query"))
    assert vectors == [[0.1, 0.2]] and model == "embedder:text-embedding-3-small"
    assert "extra_body" not in calls[0]


# ── Memory: search that works with no embedder (the original bug) ─────────────

def test_keyword_search_works_with_no_embedding_service():
    run(memory.store("Sid Vicious single drops on November 14", kind="fact"))
    run(memory.store("Sun prefers plain prose with no dashes", kind="preference"))
    found = run(memory.search("when does the Sid Vicious single drop?"))
    assert found and found[0]["content"].startswith("Sid Vicious single")
    assert found[0]["matched"] == "keyword"


def test_stemming_matches_word_variants():
    run(memory.store("The deadline for the Coursera module is Friday", kind="deadline"))
    assert run(memory.search("deadlines"))[0]["kind"] == "deadline"


@pytest.mark.parametrize("query", ['what\'s up? (test) AND OR NOT * "', "DROP TABLE memory_items; --", "", "!!!"])
def test_weird_queries_never_crash(query):
    run(memory.store("ordinary fact", kind="fact"))
    run(memory.search(query))           # must not raise


def test_kinds_are_validated_and_empty_is_refused():
    with pytest.raises(ValueError):
        run(memory.store("x", kind="gossip"))
    with pytest.raises(ValueError):
        run(memory.store("   ", kind="fact"))


def test_durable_duplicates_are_merged_but_exchanges_are_not():
    a = run(memory.store("Budget is 500", kind="fact"))
    assert run(memory.store("  Budget is 500  ", kind="fact")) == a
    e1 = run(memory.store("hi", kind="exchange"))
    assert run(memory.store("hi", kind="exchange")) != e1


def test_saved_facts_outrank_old_chat_with_the_same_words():
    # The chat line is the SHORTER, tighter keyword match, so plain keyword ranking puts it
    # first. Only the "facts beat chat" weighting can put the saved fact on top.
    run(memory.store("For the album going forward the person handling every mixing engineer duty is Dre from Atlanta", kind="fact"))
    run(memory.store("mixing engineer Dre", kind="exchange"))
    with memory._db() as db:
        assert memory._keyword_ranks(db, "mixing engineer", None, 5)[0] == 2     # chat wins on keywords alone
    assert run(memory.search("mixing engineer"))[0]["kind"] == "fact"


# ── Memory: meaning search and model safety ────────────────────────────────────

def test_meaning_search_finds_text_with_no_shared_words(monkeypatch):
    monkeypatch.setattr(llm, "embed", fake_embedder())
    run(memory.store("The budget for the single rollout is 500 dollars", kind="fact"))
    run(memory.store("Standup meeting moved to Tuesday", kind="fact"))
    found = run(memory.search("how expensive is it"))
    assert found[0]["content"].startswith("The budget") and found[0]["matched"] == "meaning"


def test_both_methods_agreeing_ranks_highest(monkeypatch):
    monkeypatch.setattr(llm, "embed", fake_embedder())
    run(memory.store("song budget notes", kind="note"))
    run(memory.store("Album cost estimate is high", kind="note"))
    top = run(memory.search("album cost"))[0]
    assert top["content"].startswith("Album cost") and top["matched"] == "keyword+meaning"


def test_vectors_from_different_models_are_never_compared(monkeypatch):
    # Same length vectors from two DIFFERENT models: only the model tag can keep them apart.
    monkeypatch.setattr(llm, "embed", fake_embedder(model="old:v1", vectorize=lambda t: [1.0, 0.0, 0.0, 0.0]))
    run(memory.store("old model memory about money", kind="fact"))
    monkeypatch.setattr(llm, "embed", fake_embedder(model="new:v2", vectorize=lambda t: [1.0, 0.0, 0.0, 0.0]))
    run(memory.store("new model memory", kind="fact"))
    found = run(memory.search("zzz unrelated words"))       # no keyword hits; only vectors could match
    assert [r["content"] for r in found] == ["new model memory"]


def test_reembed_moves_everything_to_the_current_model(monkeypatch):
    run(memory.store("made with no embedder", kind="fact"))
    run(memory.store("raw chat", kind="exchange"))
    monkeypatch.setattr(llm, "embed", fake_embedder(model="new:v2"))
    count, model = run(memory.reembed())
    assert (count, model) == (1, "new:v2")                  # durable only; exchanges skipped by default
    assert memory.stats()["embedded_by_model"] == {"new:v2": 1}


def test_reembed_stops_if_the_model_changes_mid_run(monkeypatch):
    for i in range(3):
        run(memory.store(f"fact number {i}", kind="fact"))
    models = iter(["a:1", "b:2", "b:2"])

    async def flaky(texts, purpose="passage"):
        return [[1.0, 0.0] for _ in texts], next(models)

    monkeypatch.setattr(llm, "embed", flaky)
    count, model = run(memory.reembed(batch=1))
    assert (count, model) == (0, "a:1")                     # refused to mix models


def test_exchanges_are_not_embedded_by_default(monkeypatch):
    calls = []
    monkeypatch.setattr(llm, "embed", fake_embedder(calls=calls))
    run(memory.store("chat line", kind="exchange"))
    run(memory.store("a fact", kind="fact"))
    assert [c[0] for c in calls] == [["a fact"]] and calls[0][1] == "passage"


# ── Memory: lifecycle ──────────────────────────────────────────────────────────

def test_old_memory_database_is_upgraded_without_losing_anything():
    con = sqlite3.connect(config.MEMORY_DB)
    con.executescript("""
        CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
            source TEXT NOT NULL DEFAULT 'agent', content TEXT NOT NULL, embedding TEXT NOT NULL);
        INSERT INTO memories (ts, source, content, embedding) VALUES (1.0, 'agent', 'Sun lives in Stonecrest', '[0.1]');
        INSERT INTO memories (ts, source, content, embedding) VALUES (2.0, 'telegram', '[telegram] User: hi', '[0.2]');
    """)
    con.commit()
    con.close()

    assert run(memory.search("Stonecrest"))[0]["kind"] == "note"
    stats = memory.stats()
    assert stats["by_kind"] == {"note": 1, "exchange": 1} and stats["legacy_table_kept"]
    memory.stats()                                          # opening again must not duplicate rows
    assert memory.stats()["total"] == 2
    con = sqlite3.connect(config.MEMORY_DB)
    assert con.execute("PRAGMA user_version").fetchone()[0] == memory.SCHEMA_VERSION
    assert con.execute("SELECT COUNT(*) FROM memories_legacy").fetchone()[0] == 2
    con.close()


def test_prune_only_removes_old_unpinned_exchanges():
    old = time.time() - 200 * 86_400
    run(memory.store("old chat", kind="exchange"))
    run(memory.store("old fact", kind="fact"))
    run(memory.store("old pinned chat", kind="exchange", pinned=True))
    con = sqlite3.connect(config.MEMORY_DB)
    con.execute("UPDATE memory_items SET ts = ?", (old,))
    con.commit()
    con.close()
    run(memory.store("new chat", kind="exchange"))
    assert memory.prune_exchanges(90) == 1
    assert sorted(memory.stats()["by_kind"].items()) == [("exchange", 2), ("fact", 1)]


def test_upcoming_shows_soon_and_overdue_only():
    now = time.time()
    run(memory.store("Mix due", kind="deadline", due_ts=now + 3 * 86_400))
    run(memory.store("Taxes due", kind="deadline", due_ts=now + 60 * 86_400))
    run(memory.store("Invoice overdue", kind="task", due_ts=now - 2 * 86_400))
    assert [r["content"] for r in memory.upcoming(7)] == ["Invoice overdue", "Mix due"]


def test_recall_brings_relevant_durable_memories_and_deadlines():
    run(memory.store("[cli] User: talk about the studio", kind="exchange"))
    run(memory.store("The studio is booked at Patchwerk", kind="fact"))
    run(memory.store("Send stems to Dre", kind="task", due_ts=time.time() + 86_400))
    rows = run(memory.recall("book the studio"))
    kinds = [r["kind"] for r in rows]
    assert "exchange" not in kinds and "fact" in kinds and "task" in kinds
    prompt = memory.format_for_prompt(rows)
    assert "What you remember" in prompt and "Patchwerk" in prompt and "may be out of date" in prompt


def test_forget():
    mid = run(memory.store("temporary", kind="note"))
    assert memory.forget(mid) is True and memory.forget(mid) is False
    assert run(memory.search("temporary")) == []


# ── Memory tools ───────────────────────────────────────────────────────────────

def test_memory_tools_kinds_dates_and_risk():
    out = run(registry.call("memory_store", {"content": "Pay studio", "kind": "deadline", "due": "2030-01-15"}))
    assert "deadline" in out
    row = memory.recent(1)[0]
    assert datetime.fromtimestamp(row["due_ts"]).date().isoformat() == "2030-01-15"
    assert run(registry.call("memory_store", {"content": "x", "kind": "deadline", "due": "next week"})).startswith("Tool error")
    assert registry.get("memory_forget").risk_for({}) == registry.ASK
    assert registry.get("memory_upcoming").risk_for({}) == registry.SAFE
    assert "Pay studio" in run(registry.call("memory_search", {"query": "studio", "kind": "deadline"}))


# ── Agent: recall, usage, exchanges ────────────────────────────────────────────

@pytest.fixture
def scripted(monkeypatch):
    systems = []

    def install(*replies):
        script = list(replies)

        async def chat(messages, tools=None, system="", **k):
            # What a PRIVATE brain receives: the system prompt plus the private context.
            systems.append(system + k.get("private_context", ""))
            return script.pop(0)

        monkeypatch.setattr(agent.llm, "chat", chat)
        return systems
    return install


def answer(text, **usage):
    return {"content": text, "tool_calls": [], "finish_reason": "stop", "provider": "primary", "brain": "primary",
            "model": "claude-sonnet-5-5", "provider_name": "anthropic", "fallbacks": ["backup: skipped"],
            "usage": {"input_tokens": usage.get("i", 0), "output_tokens": usage.get("o", 0), "cached_input_tokens": 0},
            "cost_usd": usage.get("cost")}


def test_agent_starts_each_run_knowing_relevant_memories(scripted):
    run(memory.store("Sid Vicious release date is November 14", kind="fact"))
    systems = scripted(answer("Nov 14"))
    run(agent.run("when is the Sid Vicious release?", source="cli"))
    assert "Sid Vicious release date is November 14" in systems[0]


def test_memory_failure_never_breaks_a_run(scripted, monkeypatch):
    async def broken(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(memory, "recall", broken)
    monkeypatch.setattr(memory, "store", broken)
    scripted(answer("still here"))
    assert run(agent.run("hello", source="cli")) == "still here"


def test_exchanges_are_saved_as_exchanges(scripted):
    scripted(answer("pong"))
    run(agent.run("ping", source="cli"))
    assert memory.stats()["by_kind"] == {"exchange": 1}


def test_token_counts_are_logged_readable_and_summed(scripted):
    scripted(answer("one", i=1200, o=300, cost=0.0081), answer("two", i=800, o=100))
    ctx1, ctx2 = RunContext(source="cli"), RunContext(source="cli")
    run(agent.run("a", ctx=ctx1))
    run(agent.run("b", ctx=ctx2))
    detail = events.session_events(ctx1.session_id)[1]["detail"]
    assert detail["input_tokens"] == 1200 and detail["output_tokens"] == 300   # not masked as "***"
    rows = events.usage_summary(30)
    assert len(rows) == 1
    r = rows[0]
    assert (r["calls"], r["input_tokens"], r["output_tokens"], r["unpriced_calls"]) == (2, 2000, 400, 1)
    assert r["cost_usd"] == pytest.approx(0.0081)
    text = events.format_usage(rows, "t")
    assert "2,000 in / 400 out" in text and "1 calls have no price" in text
    trace = events.format_session(ctx1.session_id)
    assert "1,200 in, 300 out, $0.0081" in trace and "fallback: backup: skipped" in trace


def test_real_secrets_are_still_masked():
    ctx = RunContext(source="cli")
    events.record(ctx, "tool_call", args={"api_key": "sk-1", "api_keys": ["k1", "k2"], "auth": {"user": "x"},
                                          "max_tokens": 50, "input_tokens": 7})
    a = events.session_events(ctx.session_id)[0]["detail"]["args"]
    assert a == {"api_key": "***", "api_keys": "***", "auth": "***", "max_tokens": 50, "input_tokens": 7}


# ── Paperclip cost reporting ───────────────────────────────────────────────────

AGENT_GUID = "11111111-2222-3333-4444-555555555555"
ISSUE_GUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
RUN_GUID = "99999999-8888-7777-6666-555555555555"


def usage_row(**over):
    row = {"brain": "primary", "model": "claude-sonnet-5-5", "provider": "anthropic", "calls": 2,
           "input_tokens": 1000, "output_tokens": 200, "cached_input_tokens": 0, "cost_usd": 0.0234, "unpriced_calls": 0}
    row.update(over)
    return row


def test_cost_events_match_paperclips_real_validator():
    from core import paperclip
    ev = paperclip.build_cost_events([usage_row()], AGENT_GUID, ISSUE_GUID, RUN_GUID)[0]
    assert ev["agentId"] == AGENT_GUID and ev["issueId"] == ISSUE_GUID and ev["heartbeatRunId"] == RUN_GUID
    assert ev["costCents"] == 2 and ev["costStatus"] == "reported" and ev["billingType"] == "metered_api"
    assert datetime.fromisoformat(ev["occurredAt"].replace("Z", "+00:00")).tzinfo is not None


def test_unknown_prices_are_reported_as_unpriced_not_guessed():
    from core import paperclip
    ev = paperclip.build_cost_events([usage_row(cost_usd=None, unpriced_calls=2)], AGENT_GUID, "", "")[0]
    assert ev["costCents"] == 0 and ev["costStatus"] == "unpriced"
    assert "issueId" not in ev and "heartbeatRunId" not in ev


def test_partly_priced_usage_is_not_reported_as_complete():
    from core import paperclip
    ev = paperclip.build_cost_events([usage_row(unpriced_calls=1)], AGENT_GUID, "", "")[0]
    assert ev["costStatus"] == "unpriced"


def test_non_guid_agent_or_empty_usage_reports_nothing():
    from core import paperclip
    assert paperclip.build_cost_events([usage_row()], "agent-42", "", "") == []
    assert paperclip.build_cost_events([usage_row(input_tokens=0, output_tokens=0)], AGENT_GUID, "", "") == []


def test_heartbeat_reports_its_spend_to_paperclip(scripted, monkeypatch):
    from test_paperclip import FakePaperclip, issue, heartbeat, reply, call
    fake = FakePaperclip([issue(ISSUE_GUID)])
    fake.me["id"] = AGENT_GUID
    for i in fake.issues.values():
        i["assignee"] = AGENT_GUID
    finish = reply("", call("paperclip_finish", status="done", comment="ok"))
    finish.update(brain="primary", model="claude-sonnet-5-5", provider_name="anthropic",
                  usage={"input_tokens": 4000, "output_tokens": 500, "cached_input_tokens": 0}, cost_usd=0.0195)
    scripted(finish, answer("done", i=1000, o=100, cost=0.0045))
    result = heartbeat(fake, run_id=RUN_GUID)
    assert result.outcome == "finished"
    assert len(fake.costs) == 1
    cost = fake.costs[0]
    assert (cost["inputTokens"], cost["outputTokens"], cost["costCents"]) == (5000, 600, 2)
    assert cost["issueId"] == ISSUE_GUID and cost["heartbeatRunId"] == RUN_GUID


def test_cost_report_failure_never_fails_the_heartbeat(scripted):
    from test_paperclip import FakePaperclip, issue, heartbeat, reply, call
    fake = FakePaperclip([issue(ISSUE_GUID)])
    fake.me["id"] = AGENT_GUID
    for i in fake.issues.values():
        i["assignee"] = AGENT_GUID
    finish = reply("", call("paperclip_finish", status="done", comment="ok"))
    finish["usage"] = {"input_tokens": 10, "output_tokens": 1, "cached_input_tokens": 0}
    scripted(finish, answer("done", i=1, o=1))

    async def refuse(r):
        from aiohttp import web
        return web.json_response({"error": "nope"}, status=500)

    fake.cost_event = refuse
    result = heartbeat(fake)
    assert result.outcome == "finished" and fake.costs == []
    statuses = [r["status"] for r in events.session_events(result.session_id) if r["kind"] == "heartbeat"]
    assert "cost_error" in statuses


# ── Circuit breaker ────────────────────────────────────────────────────────────

def test_a_failed_brain_is_skipped_until_its_cooldown_ends(keys, monkeypatch):
    write_brains(GOOD)
    tried = []
    primary_works = {"value": False}

    async def call(brain, *a):
        tried.append(brain.name)
        if brain.name == "primary" and not primary_works["value"]:
            raise ConnectionError("down")
        return {"content": "ok", "tool_calls": [], "finish_reason": "stop",
                "usage": {"input_tokens": 1, "output_tokens": 1, "cached_input_tokens": 0}}

    monkeypatch.setattr(llm, "_call", call)
    clock = {"now": 1000.0}
    monkeypatch.setattr(llm.time, "monotonic", lambda: clock["now"])
    msg = [{"role": "user", "content": "x"}]

    run(llm.chat(msg))                                  # primary fails, backup answers
    run(llm.chat(msg))                                  # primary is cooling: backup straight away
    assert tried == ["primary", "backup", "backup"]

    clock["now"] += llm.BRAIN_COOLDOWN_SECONDS + 1      # cooldown over: primary gets another chance
    primary_works["value"] = True
    assert run(llm.chat(msg))["brain"] == "primary"


def test_when_every_brain_is_cooling_they_are_still_tried(keys, monkeypatch):
    write_brains(GOOD)
    tried = []

    async def call(brain, *a):
        tried.append(brain.name)
        return {"content": "ok", "tool_calls": [], "finish_reason": "stop",
                "usage": {"input_tokens": 1, "output_tokens": 1, "cached_input_tokens": 0}}

    monkeypatch.setattr(llm, "_call", call)
    setup = brains.load()
    llm._mark_down(setup.brains["primary"])
    llm._mark_down(setup.brains["backup"])
    assert run(llm.chat([{"role": "user", "content": "x"}]))["brain"] == "primary"


def test_a_down_embedder_is_not_retried_on_every_message(keys, monkeypatch):
    write_brains(GOOD)
    attempts = []

    class Embeddings:
        async def create(self, **params):
            attempts.append(1)
            raise ConnectionError("ollama not running")

    monkeypatch.setattr(llm, "_openai_client", lambda b, **k: SimpleNamespace(embeddings=Embeddings()))
    from conftest import REAL_EMBED
    assert run(REAL_EMBED(["a"])) is None
    assert run(REAL_EMBED(["b"])) is None
    assert len(attempts) == 1


def test_clients_use_short_timeouts_and_few_retries(monkeypatch):
    seen = {}

    class FakeOpenAI:
        def __init__(self, **kw):
            seen.update(kw)

    import openai
    monkeypatch.setattr(openai, "AsyncOpenAI", FakeOpenAI)
    llm._openai_client(brains.Brain("x", "openai_compat", "m", "http://localhost:1/v1", no_key=True),
                       timeout=llm.EMBED_TIMEOUT_SECONDS, retries=0)
    assert seen["timeout"] == llm.EMBED_TIMEOUT_SECONDS and seen["max_retries"] == 0


# ── Backup command ─────────────────────────────────────────────────────────────

def test_backup_command_works_through_main_py(monkeypatch, capsys):
    """`python main.py backup create` used to print "Unknown command: backup"."""
    from cli import backup
    made = []
    monkeypatch.setattr(backup, "create", lambda include_data=True: made.append(include_data))
    backup.main(["create", "--no-data"])
    backup.main(["create"])
    assert made == [False, True]
    import sys as _sys
    monkeypatch.setattr(_sys, "argv", ["main.py", "backup"])
    backup.main([])                                     # no subcommand: usage, not a crash
    assert "Usage" in capsys.readouterr().out
