# Lesson 02 — Talking to LLMs

## What an LLM API Actually Is

An LLM (Large Language Model) is just a function: feed in text, get text back. The API is an HTTP endpoint that wraps that function. When you call it, you send a list of **messages** and get one back.

Every major provider (OpenAI, Anthropic, NVIDIA NIM, Ollama, OpenRouter) works the same basic way — though their exact formats differ slightly. GawdBotE handles all of them in `core/llm.py`.

---

## Messages and Roles

Every message has a **role** that tells the LLM who said it:

| Role | Who | Purpose |
|------|-----|---------|
| `system` | You (developer) | Sets the AI's personality, rules, and capabilities |
| `user` | The human | What the person actually said |
| `assistant` | The AI | What the AI said back |
| `tool` | Your code | The result of a tool the AI called |

A real conversation looks like this:

```python
messages = [
    {"role": "system",    "content": "You are GawdBotE. Be direct and helpful."},
    {"role": "user",      "content": "What files are in my project?"},
    {"role": "assistant", "content": None,  # AI decided to call a tool
     "tool_calls": [{"id": "call_1", "function": {"name": "list_project_files", "arguments": "{}"}}]},
    {"role": "tool",      "tool_call_id": "call_1", "content": "main.py\nconfig.py\ncore/agent.py"},
    {"role": "assistant", "content": "Your project has 3 files: main.py, config.py, core/agent.py"}
]
```

This conversation history is what you send on every API call. The LLM sees the whole thing each time — there's no magic memory inside the model.

---

## Tokens — The Currency of LLMs

LLMs don't read words, they read **tokens** — chunks of text roughly 3-4 characters each. Every token costs a small amount of money and counts against the **context window** (maximum input size).

```
"Hello world"  →  ["Hello", " world"]  =  2 tokens
"GawdBotE"     →  ["Gawd", "Bot", "E"] =  3 tokens
```

**Context window** = how much text the model can "see" at once. Modern models like GPT-4o have 128k tokens (~100k words). When the conversation gets too long, you have to summarize or trim old messages.

---

## The OpenAI-Compatible API

OpenAI invented the standard. Now almost every provider speaks the same format:

```python
from openai import AsyncOpenAI

client = AsyncOpenAI(
    base_url="https://integrate.api.nvidia.com/v1",  # any provider URL
    api_key="your-key"
)

response = await client.chat.completions.create(
    model="meta/llama-3.3-70b-instruct",
    messages=[
        {"role": "system", "content": "You are helpful."},
        {"role": "user",   "content": "What is 2+2?"}
    ]
)

print(response.choices[0].message.content)  # "4"
```

**This exact same code works for:** NVIDIA NIM, Ollama, OpenAI, OpenRouter — just change `base_url` and `api_key`. That's why GawdBotE can fall back between providers so easily.

---

## How GawdBotE Calls the LLM

Open `core/llm.py`. The key function is `chat()`:

```python
async def chat(messages, tools=None, system="", **kwargs) -> dict:
    """
    Send a chat request with automatic provider fallback.
    Returns normalized response dict: content, tool_calls, finish_reason, provider
    """
    for provider in config.LLM_PROVIDERS:   # try nvidia, then openrouter, then ollama
        try:
            return await _call_openai_compat(provider, messages, tools=tools)
        except Exception as exc:
            log.warning("Provider failed — %s: %s", provider, exc)
            # try next provider...

    raise RuntimeError("All LLM providers failed")
```

The function **normalizes** the response — regardless of which provider answered, you always get back the same dict shape: `{content, tool_calls, finish_reason, provider}`. The agent loop doesn't need to know or care which provider was used.

---

## What "Temperature" and Other Parameters Mean

When calling the API you can pass extra parameters:

| Parameter | What it does | Typical value |
|-----------|-------------|---------------|
| `temperature` | Randomness (0=deterministic, 2=chaotic) | 0.7 for chat, 0 for code |
| `max_tokens` | Max length of the response | 4096 |
| `top_p` | Nucleus sampling (alternative to temperature) | 0.95 |

GawdBotE uses defaults for most of these — the LLM's defaults are usually fine.

---

## Anthropic is Different

Anthropic's Claude uses a slightly different API format. The key differences:

1. `system` is a top-level parameter, not a message
2. Tool definitions use `input_schema` instead of `parameters`
3. Tool results come back as content blocks, not a simple string

GawdBotE handles this in `_call_anthropic()` in `core/llm.py` — it translates the OpenAI-style tool list into Anthropic format and translates the response back. That's why you can add Anthropic to your `LLM_PROVIDERS` without changing anything else.

---

## Exercise

Open a Python shell and make a bare API call yourself:

```python
import asyncio
from openai import AsyncOpenAI

async def test():
    # Using Ollama locally (free, no key needed)
    client = AsyncOpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
    resp = await client.chat.completions.create(
        model="llama3.3",
        messages=[{"role": "user", "content": "Say hello in exactly 5 words."}]
    )
    print(resp.choices[0].message.content)
    print(f"Tokens used: {resp.usage.total_tokens}")

asyncio.run(test())
```

Then look at `core/llm.py` and find where `_call_openai_compat` is. Notice it does exactly this — just wrapped in a try/except and with normalization at the end.

---

## Key Takeaways

- LLM APIs are HTTP endpoints — you send a list of messages, you get one back
- Roles: `system` (rules), `user` (human), `assistant` (AI), `tool` (tool results)
- The whole conversation is sent on every call — the model has no built-in memory
- Almost every provider speaks the OpenAI format — just swap `base_url` and `api_key`
- Tokens are the unit of cost and context; ~4 chars per token
