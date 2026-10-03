"""
The LLM layer: talk to whichever brain is first in line, fall back to the next on failure.

Everything above this file speaks ONE message format (OpenAI style). Each provider
"adapter" below translates to and from its own format, like an HL7 interface engine
translating between two EHRs so neither side has to know the other's dialect.

Public functions:
  chat(messages, tools, system, chain="main")  -> normalized reply dict
  describe_image(png_base64, question)         -> text, using the vision chain
  embed(texts, purpose)                        -> (vectors, model_id) or None, using the embedding chain

A normalized reply always has:
  content, tool_calls, finish_reason, provider (the brain name), brain, model,
  usage {input_tokens, output_tokens, cached_input_tokens}, cost_usd (None if price unknown),
  fallbacks (why earlier brains in the chain were skipped or failed)
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

from core import brains as brains_mod
from core.brains import Brain

log = logging.getLogger(__name__)


# ── Clients ────────────────────────────────────────────────────────────────────
# The client libraries default to a 10 minute timeout and 2 automatic retries. With a
# fallback chain behind us that only delays switching to a working brain, so we keep
# timeouts short and let the chain do the retrying.
CHAT_TIMEOUT_SECONDS = 120
EMBED_TIMEOUT_SECONDS = 15


def _openai_client(brain: Brain, timeout: float = CHAT_TIMEOUT_SECONDS, retries: int = 1):
    from openai import AsyncOpenAI
    return AsyncOpenAI(base_url=brain.base_url, api_key=brain.secret() or "none", timeout=timeout, max_retries=retries)


def _anthropic_client(brain: Brain):
    import anthropic
    return anthropic.AsyncAnthropic(api_key=brain.secret(), timeout=CHAT_TIMEOUT_SECONDS, max_retries=1)


# ── Circuit breaker ────────────────────────────────────────────────────────────
# Like downtime procedures: once a system is confirmed down, stop sending every order to
# it and use the backup until a set re check time. A brain that just failed is skipped for
# BRAIN_COOLDOWN_SECONDS. If EVERY brain in a chain is cooling down, they are tried anyway,
# because a maybe working brain beats a guaranteed failure.
BRAIN_COOLDOWN_SECONDS = 60
_down_until: dict[str, float] = {}


def _cooling(brain: Brain) -> bool:
    return _down_until.get(brain.name, 0.0) > time.monotonic()


def _mark_down(brain: Brain) -> None:
    _down_until[brain.name] = time.monotonic() + BRAIN_COOLDOWN_SECONDS


def _mark_up(brain: Brain) -> None:
    _down_until.pop(brain.name, None)


def _in_order(brains: list[Brain]) -> list[Brain]:
    """Healthy brains first, cooling ones last, original order kept within each group."""
    return [b for b in brains if not _cooling(b)] + [b for b in brains if _cooling(b)]


def _usage(tokens_in, tokens_out, cached=0) -> dict:
    return {"input_tokens": int(tokens_in or 0), "output_tokens": int(tokens_out or 0),
            "cached_input_tokens": int(cached or 0)}


# ── OpenAI compatible adapter (OpenAI, OpenRouter, NVIDIA NIM, Ollama, Groq, ...) ─

async def _call_openai_compat(brain: Brain, messages: list[dict], tools: Optional[list] = None, system: str = "") -> dict:
    msgs = messages
    if system and (not msgs or msgs[0].get("role") != "system"):
        msgs = [{"role": "system", "content": system}] + list(messages)
    params: dict = {"model": brain.model, "messages": msgs}
    if tools:
        params["tools"] = tools
        params["tool_choice"] = "auto"
    resp = await _openai_client(brain).chat.completions.create(**params)
    choice = resp.choices[0]
    u = getattr(resp, "usage", None)
    cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) if u else 0
    return {
        "content": choice.message.content or "",
        "tool_calls": [
            {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
            for tc in (choice.message.tool_calls or [])
        ],
        "finish_reason": choice.finish_reason,
        "usage": _usage(getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0), cached),
    }


# ── Anthropic adapter ──────────────────────────────────────────────────────────

def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """
    Translate the agent's internal (OpenAI shaped) messages into Anthropic's format.

    The two APIs describe a tool call differently:
      OpenAI:    assistant message with "tool_calls", then a message with role "tool"
      Anthropic: assistant message with "tool_use" blocks, then a USER message holding
                 "tool_result" blocks

    System messages are skipped because Anthropic takes the system prompt as a separate
    field. Neighboring messages from the same role are merged, since Anthropic wants
    roles to alternate.
    """
    out: list[dict] = []

    def push(role: str, blocks: list[dict]) -> None:
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": list(blocks)})

    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        if role == "tool":
            push("user", [{"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": str(m.get("content", ""))}])
        elif role == "assistant":
            blocks: list[dict] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls") or []:
                fn = tc["function"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                blocks.append({"type": "tool_use", "id": tc["id"], "name": fn["name"], "input": args})
            push("assistant", blocks)
        elif role == "user":
            content = m.get("content", "")
            if isinstance(content, list):
                blocks = content
            elif content:
                blocks = [{"type": "text", "text": str(content)}]
            else:
                blocks = []
            push("user", blocks)
    return out


def _default_anthropic_brain() -> Brain:
    setup = brains_mod.load()
    for b in setup.brains.values():
        if b.kind == "anthropic":
            return b
    import config
    return Brain("anthropic", "anthropic", config.ANTHROPIC_MODEL, api_key="ANTHROPIC_API_KEY")


async def _call_anthropic(messages: list[dict], tools: Optional[list] = None, system: str = "",
                          brain: Optional[Brain] = None, **_) -> dict:
    brain = brain or _default_anthropic_brain()
    ant_tools = []
    for t in tools or []:
        fn = t.get("function", t)
        ant_tools.append({"name": fn["name"], "description": fn.get("description", ""),
                          "input_schema": fn.get("parameters", {"type": "object", "properties": {}})})

    params: dict = {"model": brain.model, "messages": _to_anthropic_messages(messages), "max_tokens": brain.max_tokens}
    if system:
        params["system"] = system
    if ant_tools:
        params["tools"] = ant_tools

    resp = await _anthropic_client(brain).messages.create(**params)
    u = getattr(resp, "usage", None)
    return {
        "content": "\n".join(b.text for b in resp.content if b.type == "text"),
        # json.dumps gives real JSON. The original code used str(b.input), a Python repr
        # that json.loads rejects, so every Anthropic tool call silently got empty arguments.
        "tool_calls": [{"id": b.id, "name": b.name, "arguments": json.dumps(b.input)}
                       for b in resp.content if b.type == "tool_use"],
        "finish_reason": resp.stop_reason,
        "usage": _usage(getattr(u, "input_tokens", 0), getattr(u, "output_tokens", 0),
                        getattr(u, "cache_read_input_tokens", 0)),
    }


# ── Chat with fallback ─────────────────────────────────────────────────────────

async def _call(brain: Brain, messages, tools, system) -> dict:
    if brain.kind == "anthropic":
        return await _call_anthropic(messages, tools=tools, system=system, brain=brain)
    return await _call_openai_compat(brain, messages, tools=tools, system=system)


async def chat(messages: list[dict], tools: Optional[list] = None, system: str = "", chain: str = "main", **_) -> dict:
    """
    Send a chat request down the brain chain until one answers.
    Raises RuntimeError listing every brain's problem if none can.
    """
    problems: list[str] = []
    for brain in _in_order(brains_mod.chain(chain)):
        if tools and not brain.tools:
            problems.append(f"{brain.name}: skipped, it cannot call tools")
            continue
        ok, why = brain.available()
        if not ok:
            problems.append(f"{brain.name}: skipped, {why}")
            continue
        try:
            resp = await _call(brain, messages, tools, system)
        except Exception as exc:
            msg = f"{brain.name}: {type(exc).__name__}: {exc}"
            log.warning("Brain failed, trying the next one. %s", msg[:300])
            problems.append(msg[:300])
            _mark_down(brain)
            continue
        _mark_up(brain)
        u = resp.get("usage") or _usage(0, 0)
        resp.update(provider=brain.name, brain=brain.name, model=brain.model, provider_name=brain.provider,
                    fallbacks=problems, cost_usd=brain.cost_usd(u["input_tokens"], u["output_tokens"]))
        return resp
    detail = "\n".join(problems) or f"no brains are configured for the {chain} chain"
    raise RuntimeError(f"All LLM providers failed:\n{detail}")


# ── Vision ─────────────────────────────────────────────────────────────────────

async def describe_image(png_base64: str, question: str) -> str:
    """Ask the vision chain about an image. Accepts raw base64 or a data: URL."""
    data = png_base64.split(",", 1)[1] if png_base64.startswith("data:") else png_base64
    problems: list[str] = []
    for brain in _in_order(brains_mod.chain("vision")):
        ok, why = brain.available()
        if not ok:
            problems.append(f"{brain.name}: skipped, {why}")
            continue
        try:
            if brain.kind == "anthropic":
                msg = [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
                    {"type": "text", "text": question}]}]
                return (await _call_anthropic(msg, brain=brain))["content"]
            msg = [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
                {"type": "text", "text": question}]}]
            return (await _call_openai_compat(brain, msg))["content"]
        except Exception as exc:
            _mark_down(brain)
            problems.append(f"{brain.name}: {type(exc).__name__}: {exc}"[:300])
    raise RuntimeError("No vision brain could look at the image:\n" + ("\n".join(problems) or "none configured"))


# ── Embeddings ─────────────────────────────────────────────────────────────────

async def embed(texts: list[str], purpose: str = "passage") -> Optional[tuple[list[list[float]], str]]:
    """
    Turn texts into vectors with the first working embedding brain.
    Returns (vectors, model_id) or None when no embedder is available. model_id is stored
    with each vector, because vectors from different models cannot be compared.
    purpose: "passage" for things being stored, "query" for searches (some models care).
    """
    for brain in brains_mod.chain("embedding"):
        # Embeddings are optional (keyword search still works), so a cooling embedder is
        # simply skipped. This keeps a stopped Ollama from slowing down every message.
        ok, _ = brain.available()
        if not ok or _cooling(brain):
            continue
        try:
            extra = {"encoding_format": "float", "extra_body": {"input_type": purpose}} if brain.embed_input_type else {}
            client = _openai_client(brain, timeout=EMBED_TIMEOUT_SECONDS, retries=0)
            resp = await client.embeddings.create(model=brain.model, input=texts, **extra)
            _mark_up(brain)
            return [d.embedding for d in resp.data], f"{brain.name}:{brain.model}"
        except Exception as exc:
            _mark_down(brain)
            log.warning("Embedding brain %s failed, pausing it for %ss: %s", brain.name, BRAIN_COOLDOWN_SECONDS, exc)
    return None
