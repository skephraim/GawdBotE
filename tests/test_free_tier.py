"""
Free tier routers (FreeLLMAPI, OpenRouter free models): brains marked shares_data never see
saved memories, never embed them, and the model that actually answered is recorded.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from types import SimpleNamespace

import pytest
from aiohttp import web

import config
from core import agent, brains, events, llm, memory
from core.context import RunContext

MEMORY_TEXT = "Sun's studio door code is kept in the safe"   # stands in for something private


def run(coro):
    return asyncio.run(coro)


def write_brains(text: str) -> None:
    with open(config.BRAINS_FILE, "w", encoding="utf-8") as f:
        f.write(text)
    later = time.time() + 10 + len(text) / 997
    os.utime(config.BRAINS_FILE, (later, later))


CHAINS = """
[chains]
main = ["private", "free"]

[brains.private]
kind = "openai_compat"
base_url = "http://127.0.0.1:1/v1"
model = "paid-model"
no_key = true

[brains.free]
kind = "openai_compat"
base_url = "http://127.0.0.1:2/v1"
model = "auto"
no_key = true
shares_data = true
"""


def ok_reply(content="ok", served=None):
    return {"content": content, "tool_calls": [], "finish_reason": "stop", "served_model": served,
            "usage": {"input_tokens": 10, "output_tokens": 2, "cached_input_tokens": 0}}


@pytest.fixture
def seen(monkeypatch):
    """Record the system prompt each brain actually received."""
    calls: list[tuple[str, str]] = []
    behaviour = {"private_fails": False}

    async def fake_call(brain, messages, tools, system):
        calls.append((brain.name, system))
        if brain.name == "private" and behaviour["private_fails"]:
            raise ConnectionError("paid provider down")
        return ok_reply(served="groq/llama-fast" if brain.name == "free" else None)

    monkeypatch.setattr(llm, "_call", fake_call)
    write_brains(CHAINS)
    return calls, behaviour


# ── Config ─────────────────────────────────────────────────────────────────────

def test_shares_data_is_parsed_and_shown():
    write_brains(CHAINS)
    assert brains.load().brains["free"].shares_data is True
    assert brains.load().brains["private"].shares_data is False
    assert "shares your data: memories withheld" in brains.describe()


# ── Memories are withheld per brain ────────────────────────────────────────────

def test_private_brain_receives_memories(seen):
    calls, _ = seen
    run(llm.chat([{"role": "user", "content": "hi"}], system="SYS", private_context="\nMEM"))
    assert calls == [("private", "SYS\nMEM")]


def test_shared_brain_never_receives_memories(seen):
    calls, _ = seen
    brains.use("free")
    resp = run(llm.chat([{"role": "user", "content": "hi"}], system="SYS", private_context="\nMEM"))
    assert calls == [("free", "SYS")]
    assert resp["memory_withheld"] is True and resp["shares_data"] is True


def test_fallback_from_private_to_shared_still_withholds(seen):
    calls, behaviour = seen
    behaviour["private_fails"] = True
    resp = run(llm.chat([{"role": "user", "content": "hi"}], system="SYS", private_context="\nMEM"))
    assert calls == [("private", "SYS\nMEM"), ("free", "SYS")]
    assert resp["brain"] == "free" and resp["memory_withheld"] is True


def test_owner_can_explicitly_allow_sharing(seen, monkeypatch):
    calls, _ = seen
    monkeypatch.setattr(config, "MEMORY_TO_SHARED_BRAINS", True)
    brains.use("free")
    resp = run(llm.chat([{"role": "user", "content": "hi"}], system="SYS", private_context="\nMEM"))
    assert calls == [("free", "SYS\nMEM")] and resp["memory_withheld"] is False


def test_no_memories_means_nothing_marked_withheld(seen):
    brains.use("free")
    assert run(llm.chat([{"role": "user", "content": "hi"}], system="SYS"))["memory_withheld"] is False


# ── Served model ───────────────────────────────────────────────────────────────

def _fake_openai(model_attr):
    class Completions:
        async def create(self, **params):
            msg = SimpleNamespace(content="hi", tool_calls=None)
            r = SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")],
                                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, prompt_tokens_details=None))
            if model_attr is not None:
                r.model = model_attr
            return r
    return lambda b, **k: SimpleNamespace(chat=SimpleNamespace(completions=Completions()))


@pytest.mark.parametrize("served, expected", [("cerebras/qwen-3", "cerebras/qwen-3"), (None, "auto"), (123, "auto"), ("", "auto")])
def test_served_model_comes_from_the_response(monkeypatch, served, expected):
    monkeypatch.setattr(llm, "_openai_client", _fake_openai(served))
    b = brains.Brain("free", "openai_compat", "auto", "http://x/v1", no_key=True)
    assert run(llm._call_openai_compat(b, [{"role": "user", "content": "x"}]))["served_model"] == expected


# ── Embeddings ─────────────────────────────────────────────────────────────────

EMBED_CHAINS = """
[chains]
main = []
embedding = ["free-embed"]

[brains.free-embed]
kind = "openai_compat"
base_url = "http://127.0.0.1:3/v1"
model = "auto"
no_key = true
tools = false
shares_data = true
"""


def _embedder(served, calls):
    class Embeddings:
        async def create(self, **params):
            calls.append(params)
            return SimpleNamespace(model=served, data=[SimpleNamespace(embedding=[0.1, 0.2])])
    return lambda b, **k: SimpleNamespace(embeddings=Embeddings())


def test_memories_are_never_sent_to_a_shared_embedder(monkeypatch):
    from conftest import REAL_EMBED
    calls = []
    monkeypatch.setattr(llm, "_openai_client", _embedder("bge-m3", calls))
    write_brains(EMBED_CHAINS)
    assert run(REAL_EMBED([MEMORY_TEXT])) is None
    assert calls == []                                    # the text never left


def test_shared_embedder_works_when_allowed_and_is_tagged_by_served_model(monkeypatch):
    from conftest import REAL_EMBED
    calls = []
    monkeypatch.setattr(llm, "_openai_client", _embedder("bge-m3", calls))
    monkeypatch.setattr(config, "MEMORY_TO_SHARED_BRAINS", True)
    write_brains(EMBED_CHAINS)
    vectors, model = run(REAL_EMBED(["hello"]))
    assert model == "free-embed:bge-m3"                    # not "free-embed:auto"


# ── The agent, end to end ──────────────────────────────────────────────────────

@pytest.fixture
def no_exchange_store(monkeypatch):
    real_store = memory.store

    async def store(content, source="agent", kind="note", **kw):
        if kind == "exchange":
            return 0
        return await real_store(content, source=source, kind=kind, **kw)
    monkeypatch.setattr(memory, "store", store)


def test_agent_logs_served_model_and_withheld_memory(seen, no_exchange_store):
    run(memory.store(MEMORY_TEXT, kind="fact"))
    brains.use("free")
    ctx = RunContext(source="cli")
    run(agent.run("where is the studio door code?", ctx=ctx))
    reply = [e for e in events.session_events(ctx.session_id) if e["kind"] == "llm_reply"][0]["detail"]
    assert reply["served_model"] == "groq/llama-fast" and reply["memory_withheld"] is True
    trace = events.format_session(ctx.session_id)
    assert "memory withheld: free may share data" in trace and "free via groq/llama-fast" in trace
    assert events.usage_summary(1)[0]["model"] == "groq/llama-fast"


# ── Proof at the wire: a fake FreeLLMAPI records exactly what GawdBot sends ────

class FakeRouter:
    def __init__(self):
        self.bodies: list[dict] = []

    async def chat(self, request):
        body = await request.json()
        self.bodies.append(body)
        return web.json_response({
            "id": "x", "object": "chat.completion", "created": 0, "model": "cerebras/llama-free",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "done"}}],
            "usage": {"prompt_tokens": 42, "completion_tokens": 3, "total_tokens": 45},
        })

    def app(self):
        a = web.Application()
        a.router.add_post("/v1/chat/completions", self.chat)
        return a


@pytest.mark.parametrize("shares, memory_on_wire", [(True, False), (False, True)])
def test_on_the_wire_memories_only_reach_private_brains(monkeypatch, no_exchange_store, shares, memory_on_wire):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    run(memory.store(MEMORY_TEXT, kind="fact"))
    router = FakeRouter()

    async def go():
        runner = web.AppRunner(router.app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        write_brains(f"""
[chains]
main = ["free"]

[brains.free]
kind = "openai_compat"
base_url = "http://127.0.0.1:{port}/v1"
model = "auto"
no_key = true
shares_data = {str(shares).lower()}
""")
        try:
            return await agent.run("where is the studio door code?", source="cli")
        finally:
            await runner.cleanup()

    assert run(go()) == "done"
    wire = json.dumps(router.bodies)
    assert ("studio door code is kept in the safe" in wire) is memory_on_wire
    assert "where is the studio door code?" in wire          # the question itself always goes
