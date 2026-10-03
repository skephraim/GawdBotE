"""
Memory graph tests: entities, links with honest confidence, graph expansion with hub
blocking, "about" and "connect" questions, cleanup, and the schema upgrade.
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

import config
from core import graph, memory, registry


def run(coro):
    return asyncio.run(coro)


def store(text, kind="fact", about=None):
    return run(memory.store(text, kind=kind, about=about))


def db_rows(sql, params=()):
    con = sqlite3.connect(config.MEMORY_DB)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


# ── Entities and mentions ──────────────────────────────────────────────────────

def test_about_creates_entities_and_mentions():
    mid = store("Dre is mixing the Sid Vicious single", about=["Dre", "Sid Vicious single"])
    names = {r[0] for r in db_rows("SELECT name FROM entities")}
    assert names == {"Dre", "Sid Vicious single"}
    assert len(db_rows("SELECT * FROM mentions WHERE memory_id = ?", (mid,))) == 2


def test_known_entities_are_linked_automatically_in_later_memories():
    store("Dre is mixing the single", about=["Dre"])
    later = store("Studio time with dre on Friday")              # no about given, different case
    assert db_rows("SELECT e.name FROM mentions m JOIN entities e ON e.id = m.entity_id WHERE m.memory_id = ?",
                   (later,)) == [("Dre",)]


@pytest.mark.parametrize("text", ["Andre called", "send the address", "dreams of platinum", "Dreams"])
def test_entity_matching_respects_whole_words(text):
    store("Dre is mixing", about=["Dre"])
    mid = store(text)
    assert db_rows("SELECT * FROM mentions WHERE memory_id = ?", (mid,)) == []


def test_spelling_variants_become_one_entity():
    store("a", about=["Sid Vicious"])
    store("b", about=["sid  VICIOUS!"])
    assert db_rows("SELECT COUNT(*) FROM entities") == [(1,)]


@pytest.mark.parametrize("bad", ["", "   ", "!!!", "--"])
def test_nameless_entities_are_skipped_in_about(bad):
    store("something", about=[bad, "Real Name"])
    assert {r[0] for r in db_rows("SELECT name FROM entities")} == {"Real Name"}


def test_hostile_names_are_stored_as_plain_text():
    store("x", about=["Robert'); DROP TABLE entities;--"])
    assert db_rows("SELECT COUNT(*) FROM entities") == [(1,)]
    assert db_rows("SELECT COUNT(*) FROM memory_items") == [(1,)]


def test_about_list_is_capped():
    store("many things", about=[f"thing {i}" for i in range(100)])
    assert db_rows("SELECT COUNT(*) FROM entities") == [(graph.MAX_ABOUT,)]


# ── Relations and confidence ───────────────────────────────────────────────────

def test_link_is_extracted_only_when_the_memory_states_both_names():
    mid = store("Dre is mixing the Sid Vicious single")
    assert memory.link("Dre", "mixes", "Sid Vicious single", mid) == "EXTRACTED"
    other = store("Mixing starts Monday")
    assert memory.link("Dre", "starts", "Monday session", other) == "INFERRED"
    assert memory.link("Sun", "manages", "Sid Vicious") == "INFERRED"            # no evidence at all


def test_new_evidence_upgrades_confidence_but_never_lowers_it():
    assert memory.link("Sun", "manages", "Sid Vicious") == "INFERRED"
    mid = store("Sun manages Sid Vicious")
    assert memory.link("Sun", "manages", "Sid Vicious", mid) == "EXTRACTED"
    assert memory.link("Sun", "manages", "Sid Vicious") == "EXTRACTED"           # stays EXTRACTED
    assert db_rows("SELECT memory_id FROM relations") == [(mid,)]


def test_bad_links_are_refused():
    with pytest.raises(ValueError):
        memory.link("Dre", "", "Sid")
    with pytest.raises(ValueError):
        memory.link("Dre", "is", "dre")
    with pytest.raises(ValueError):
        memory.link("Dre", "mixes", "Sid", memory_id=99999)


# ── Graph expansion in search ──────────────────────────────────────────────────

def test_graph_finds_a_memory_sharing_no_words_with_the_question():
    """No embedding service at all. Keyword search finds the first memory; only the graph
    (both memories are about Dre) can find the second, which shares no words with the question."""
    store("Dre is mixing the Sid Vicious single", about=["Dre", "Sid Vicious single"])
    store("His studio rate is 150 an hour, paid weekly", about=["Dre"])
    results = run(memory.search("who is mixing the single?"))
    by_text = {r["content"]: r["matched"] for r in results}
    assert by_text["His studio rate is 150 an hour, paid weekly"] == "graph"
    assert results[0]["content"].startswith("Dre is mixing")


def test_naming_an_entity_in_the_question_pulls_its_memories():
    store("Paid on the 15th by wire transfer", about=["LearningMate"])
    store("Contract ends in December", about=["LearningMate"])
    found = {r["content"] for r in run(memory.search("anything new from learningmate"))}
    assert {"Paid on the 15th by wire transfer", "Contract ends in December"} <= found


def test_hub_entities_are_not_walked_through():
    # "Sun" is in 30 unrelated memories: a hub. A question about Dre must not drag them all in.
    for i in range(30):
        store(f"Unrelated errand number {i}", about=["Sun"])
    store("Dre is mixing the single and Sun approved it", about=["Dre", "Sun"])
    results = run(memory.search("who is mixing", top_k=10))
    assert not any(r["content"].startswith("Unrelated errand") for r in results)


def test_a_hub_named_in_the_question_still_works():
    for i in range(30):
        store(f"Errand {i} for the week", about=["Sun"])
    found = run(memory.search("what does Sun have going on", top_k=5))
    assert len(found) == 5 and all("graph" in r["matched"] for r in found)


def test_graph_respects_kind_filters():
    store("Dre is mixing", about=["Dre"])
    store("[cli] User: Dre called", kind="exchange", about=["Dre"])
    found = run(memory.search("Dre", kinds=["fact"]))
    assert {r["kind"] for r in found} == {"fact"}


def test_recall_uses_the_graph_too():
    store("Dre is mixing the Sid Vicious single", about=["Dre"])
    store("Dre prefers stems as 24 bit WAV files", kind="preference", about=["Dre"])
    rows = run(memory.recall("send the mix files to Dre"))
    assert any("24 bit WAV" in r["content"] for r in rows)


# ── About and connect ──────────────────────────────────────────────────────────

def test_about_card_shows_links_neighbors_and_memories():
    mid = store("Dre is mixing the Sid Vicious single", about=["Dre", "Sid Vicious single"])
    memory.link("Dre", "mixes", "Sid Vicious single", mid)
    store("Dre and Patchwerk confirmed Friday", about=["Dre", "Patchwerk"])
    card = memory.about_text("dre")
    assert card.startswith("Dre: mentioned in 2 memories")
    assert f"Dre mixes Sid Vicious single [EXTRACTED, from memory #{mid}]" in card
    assert "Patchwerk (1)" in card and "Sid Vicious single (1)" in card
    assert "nothing filed" in memory.about_text("Nobody Known")


def test_connect_traces_explicit_links_with_evidence():
    m1 = store("Sun manages Sid Vicious")
    memory.link("Sun", "manages", "Sid Vicious", m1)
    m2 = store("Sid Vicious works with Dre on the single")
    memory.link("Sid Vicious", "works with", "Dre", m2)
    text = memory.connect_text("Sun", "Dre")
    assert text.splitlines() == [
        "2 hops:",
        f"  Sun --manages--> Sid Vicious [EXTRACTED, memory #{m1}]",
        f"  Sid Vicious --works with--> Dre [EXTRACTED, memory #{m2}]",
    ]


def test_connect_prefers_explicit_links_over_co_mentions():
    store("Patchwerk and Dre", about=["Patchwerk", "Dre"])                 # co mention: a short path
    memory.link("Patchwerk", "books", "Engineer X")
    memory.link("Engineer X", "assists", "Dre")                            # explicit: a longer path
    with memory._db() as db:
        steps = graph.connect(db, "Patchwerk", "Dre")
    assert [s["relation"] for s in steps] == ["books", "assists"]


def test_connect_never_steps_through_a_hub():
    for i in range(30):
        store(f"Note {i}", about=["Sun"])
    store("Sun and Alpha", about=["Sun", "Alpha"])
    store("Sun and Beta", about=["Sun", "Beta"])
    assert "can't find a connection" in memory.connect_text("Alpha", "Beta")


def test_connect_edge_cases():
    store("x", about=["Alpha"])
    assert "same thing" in memory.connect_text("alpha", "ALPHA")
    assert "can't find" in memory.connect_text("Alpha", "Never Heard Of")


# ── Cleanup and upgrades ───────────────────────────────────────────────────────

def test_forgetting_a_memory_removes_what_it_alone_supported():
    keep = store("Dre is mixing", about=["Dre"])
    gone = store("Patchwerk booked by Dre", about=["Patchwerk", "Dre"])
    memory.link("Patchwerk", "hosts", "Dre", gone)
    assert memory.forget(gone)
    assert {r[0] for r in db_rows("SELECT name FROM entities")} == {"Dre"}      # Patchwerk orphaned, removed
    assert db_rows("SELECT COUNT(*) FROM relations") == [(0,)]                 # its relation went with it
    assert db_rows("SELECT memory_id FROM mentions") == [(keep,)]


def test_pruning_old_chat_cleans_the_graph():
    store("old chat about Zed", kind="exchange", about=["Zed"])
    con = sqlite3.connect(config.MEMORY_DB)
    con.execute("UPDATE memory_items SET ts = 0")
    con.commit()
    con.close()
    memory.prune_exchanges(1)
    assert db_rows("SELECT COUNT(*) FROM entities") == [(0,)]


def test_foreign_keys_are_enforced():
    with memory._db() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO mentions (memory_id, entity_id) VALUES (12345, 67890)")


def test_upgrade_from_schema_1_keeps_every_memory():
    run(memory.store("before the graph existed", kind="fact"))
    con = sqlite3.connect(config.MEMORY_DB)
    con.executescript("DROP VIEW entity_edges; DROP TABLE relations; DROP TABLE mentions; DROP TABLE entities;"
                      "PRAGMA user_version = 1;")
    con.commit()
    con.close()
    stats = memory.stats()
    assert stats["total"] == 1 and stats["graph"]["entities"] == 0
    assert db_rows("PRAGMA user_version") == [(memory.SCHEMA_VERSION,)]
    mid = store("after: Dre is mixing", about=["Dre"])
    assert memory.stats()["graph"]["mentions"] == 1 and mid == 2


# ── Tools ──────────────────────────────────────────────────────────────────────

def test_graph_tools_round_trip_and_are_safe():
    out = run(registry.call("memory_store", {"content": "Dre is mixing the single", "about": ["Dre", "single"]}))
    mid = int(out.split("#")[1].split()[0])
    assert "[EXTRACTED]" in run(registry.call("memory_link", {"subject": "Dre", "relation": "mixes",
                                                             "object": "single", "memory_id": mid}))
    assert "Dre mixes single" in run(registry.call("memory_about", {"name": "Dre"}))
    assert "1 hop" in run(registry.call("memory_connect", {"a": "Dre", "b": "single"}))
    assert run(registry.call("memory_link", {"subject": "a", "relation": "", "object": "b"})).startswith("Tool error")
    for name in ("memory_link", "memory_about", "memory_connect"):
        assert registry.get(name).risk_for({}) == registry.SAFE
    assert registry.get("memory_store").parameters["properties"]["about"] == {
        "type": "array", "items": {"type": "string"},
        "description": registry.get("memory_store").parameters["properties"]["about"]["description"]}


def test_graph_expansion_returns_only_new_memories_not_its_seeds():
    """The graph is a voter for DISCOVERIES. Handing back its own seeds would double count
    them and push the real finds down the ranking."""
    seed = store("Dre is mixing the single", about=["Dre"])
    found = store("Dre charges by the hour", about=["Dre"])
    with memory._db() as db:
        assert graph.expand(db, [seed], "who is mixing") == [found]
