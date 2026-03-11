"""
Multi-provider LLM with automatic fallback.
Providers are tried in order defined by LLM_PROVIDERS in config.
Supports: nvidia, ollama, openai, anthropic, openrouter
"""
from __future__ import annotations
import asyncio
import logging
from typing import Optional

import config

log = logging.getLogger(__name__)


# ── Provider registry ──────────────────────────────────────────────────────────

def _openai_client(base_url: str, api_key: str):
    from openai import AsyncOpenAI
    return AsyncOpenAI(base_url=base_url, api_key=api_key or "none")


def _anthropic_client():
    import anthropic
    return anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)


PROVIDER_CONFIG: dict[str, dict] = {
    "nvidia": {
        "type": "openai_compat",
        "base_url": lambda: config.NVIDIA_BASE_URL,
        "api_key": lambda: config.NVIDIA_API_KEY,
        "model": lambda: config.NVIDIA_MODEL,
    },
    "ollama": {
        "type": "openai_compat",
        "base_url": lambda: config.OLLAMA_BASE_URL,
        "api_key": lambda: "ollama",
        "model": lambda: config.OLLAMA_MODEL,
    },
    "openai": {
        "type": "openai_compat",
        "base_url": lambda: "https://api.openai.com/v1",
        "api_key": lambda: config.OPENAI_API_KEY,
        "model": lambda: config.OPENAI_MODEL,
    },
    "openrouter": {
        "type": "openai_compat",
        "base_url": lambda: config.OPENROUTER_BASE_URL,
        "api_key": lambda: config.OPENROUTER_API_KEY,
        "model": lambda: config.OPENROUTER_MODEL,
    },
    "anthropic": {
        "type": "anthropic",
        "model": lambda: config.ANTHROPIC_MODEL,
    },
}


async def _call_openai_compat(
    provider: str,
    messages: list[dict],
    tools: Optional[list] = None,
    **kwargs,
) -> dict:
    """Call an OpenAI-compatible endpoint and return a normalized response dict."""
    pc = PROVIDER_CONFIG[provider]
    client = _openai_client(pc["base_url"](), pc["api_key"]())
    model = pc["model"]()
    params = dict(model=model, messages=messages, **kwargs)
    if tools:
        params["tools"] = tools
        params["tool_choice"] = "auto"
    resp = await client.chat.completions.create(**params)
    choice = resp.choices[0]
    return {
        "content": choice.message.content or "",
        "tool_calls": [
            {
                "id": tc.id,
                "name": tc.function.name,
                "arguments": tc.function.arguments,
            }
            for tc in (choice.message.tool_calls or [])
        ],
        "finish_reason": choice.finish_reason,
        "provider": provider,
    }


async def _call_anthropic(
    messages: list[dict],
    tools: Optional[list] = None,
    system: str = "",
    **kwargs,
) -> dict:
    """Call Anthropic API and return a normalized response dict."""
    import anthropic
    client = _anthropic_client()
    model = config.ANTHROPIC_MODEL

    # Convert OpenAI-style tools to Anthropic format
    ant_tools = []
    if tools:
        for t in tools:
            fn = t.get("function", t)
            ant_tools.append({
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })

    params: dict = dict(model=model, messages=messages, max_tokens=4096)
    if system:
        params["system"] = system
    if ant_tools:
        params["tools"] = ant_tools

    resp = await client.messages.create(**params)

    text_parts = [b.text for b in resp.content if hasattr(b, "text")]
    tool_calls = [
        {"id": b.id, "name": b.name, "arguments": str(b.input)}
        for b in resp.content
        if b.type == "tool_use"
    ]
    return {
        "content": "\n".join(text_parts),
        "tool_calls": tool_calls,
        "finish_reason": resp.stop_reason,
        "provider": "anthropic",
    }


async def chat(
    messages: list[dict],
    tools: Optional[list] = None,
    system: str = "",
    **kwargs,
) -> dict:
    """
    Send a chat request with automatic provider fallback.
    Returns normalized response dict with keys: content, tool_calls, finish_reason, provider.
    Raises RuntimeError if all providers fail.
    """
    errors: list[str] = []

    for provider in config.LLM_PROVIDERS:
        if provider not in PROVIDER_CONFIG:
            log.warning("Unknown provider %r — skipping", provider)
            continue

        try:
            log.debug("Trying provider %r", provider)
            pc = PROVIDER_CONFIG[provider]

            if pc["type"] == "anthropic":
                return await _call_anthropic(messages, tools=tools, system=system, **kwargs)
            else:
                # Inject system message for openai-compat providers
                msgs = messages
                if system and (not msgs or msgs[0].get("role") != "system"):
                    msgs = [{"role": "system", "content": system}] + list(messages)
                return await _call_openai_compat(provider, msgs, tools=tools, **kwargs)

        except Exception as exc:
            err = f"{provider}: {exc}"
            log.warning("Provider failed — %s", err)
            errors.append(err)

    raise RuntimeError(f"All LLM providers failed:\n" + "\n".join(errors))


async def get_vision_client():
    """Return (client, model) for vision tasks — always prefers first available vision provider."""
    for provider in config.LLM_PROVIDERS:
        if provider == "nvidia" and config.NVIDIA_API_KEY:
            from openai import AsyncOpenAI
            return AsyncOpenAI(base_url=config.NVIDIA_BASE_URL, api_key=config.NVIDIA_API_KEY), config.NVIDIA_VISION_MODEL
        if provider == "ollama":
            from openai import AsyncOpenAI
            return AsyncOpenAI(base_url=config.OLLAMA_BASE_URL, api_key="ollama"), config.OLLAMA_VISION_MODEL
        if provider == "openai" and config.OPENAI_API_KEY:
            from openai import AsyncOpenAI
            return AsyncOpenAI(api_key=config.OPENAI_API_KEY), "gpt-4o"
    raise RuntimeError("No vision provider configured")
