# Lesson 04 — Multi-Provider LLM Fallback

## The Problem: Single Points of Failure

If your agent only calls one LLM provider, it goes down when that provider goes down. API quotas get hit. Keys expire. Services have outages. For a personal AI that runs 24/7 as a system service, this is a real problem.

GawdBotE solves this with **automatic fallback** — a chain of providers tried in order.

---

## The Provider Chain

Configured in `.env`:

```
LLM_PROVIDERS=nvidia,openrouter,ollama
```

When GawdBotE needs to call an LLM, it tries:

```
1. NVIDIA NIM  →  if it fails for any reason...
2. OpenRouter  →  if that fails too...
3. Ollama      →  local model, always available
```

This means GawdBotE almost never goes down. Even if every cloud API fails, Ollama runs locally on your machine.

---

## Why OpenRouter as the Middle Backup?

**OpenRouter** is a meta-provider — a single API key that routes to 200+ models from different companies (OpenAI, Anthropic, Meta, Mistral, Google, etc.).

The advantages:
- One API key gives you access to everything
- If one underlying model is down, you can swap to another instantly
- Often cheaper than going direct for many models
- Same OpenAI-compatible API format

This makes it the perfect backup: if NVIDIA NIM is unavailable, OpenRouter can probably find a similar model somewhere.

---

## The Provider Registry

In `core/llm.py`, each provider is defined in a simple dictionary:

```python
PROVIDER_CONFIG = {
    "nvidia": {
        "type": "openai_compat",
        "base_url": lambda: config.NVIDIA_BASE_URL,    # https://integrate.api.nvidia.com/v1
        "api_key":  lambda: config.NVIDIA_API_KEY,
        "model":    lambda: config.NVIDIA_MODEL,       # meta/llama-3.3-70b-instruct
    },
    "openrouter": {
        "type": "openai_compat",
        "base_url": lambda: config.OPENROUTER_BASE_URL,  # https://openrouter.ai/api/v1
        "api_key":  lambda: config.OPENROUTER_API_KEY,
        "model":    lambda: config.OPENROUTER_MODEL,
    },
    "ollama": {
        "type": "openai_compat",
        "base_url": lambda: config.OLLAMA_BASE_URL,    # http://localhost:11434/v1
        "api_key":  lambda: "ollama",                  # Ollama doesn't need a key
        "model":    lambda: config.OLLAMA_MODEL,
    },
    "anthropic": {
        "type": "anthropic",                           # different API format
        "model":    lambda: config.ANTHROPIC_MODEL,
    },
}
```

All the providers marked `"openai_compat"` use the same code path — just different URLs and keys. Anthropic gets its own code path because its API format is slightly different.

---

## The Fallback Logic

```python
async def chat(messages, tools=None, system="", **kwargs) -> dict:
    errors = []

    for provider in config.LLM_PROVIDERS:       # ["nvidia", "openrouter", "ollama"]
        try:
            log.debug("Trying provider %r", provider)
            pc = PROVIDER_CONFIG[provider]

            if pc["type"] == "anthropic":
                return await _call_anthropic(messages, tools=tools, system=system)
            else:
                return await _call_openai_compat(provider, messages, tools=tools)

        except Exception as exc:
            err = f"{provider}: {exc}"
            log.warning("Provider failed — %s", err)
            errors.append(err)          # record the failure, try next provider

    # If we get here, everything failed
    raise RuntimeError(f"All LLM providers failed:\n" + "\n".join(errors))
```

The `try/except` around each provider catches *any* failure — network error, invalid key, rate limit, model unavailable — and moves to the next. The errors are logged so you can diagnose issues.

---

## Response Normalization

Each provider returns a slightly different response shape. GawdBotE **normalizes** all of them to the same dict before returning:

```python
# What we always return, regardless of provider:
{
    "content":      "Here is my response...",   # the text reply
    "tool_calls":   [...],                       # list of tool calls (may be empty)
    "finish_reason": "stop",                     # why the model stopped
    "provider":     "nvidia"                     # which provider actually answered
}
```

The agent loop in `core/agent.py` never needs to know which provider was used. It always works with this same dict. This is the **adapter pattern** — different backends, one interface.

---

## Adding a New Provider

It's just a few lines. Say you want to add Groq:

**1. Add config vars** in `config.py`:
```python
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
```

**2. Add to the registry** in `core/llm.py`:
```python
"groq": {
    "type": "openai_compat",
    "base_url": lambda: "https://api.groq.com/openai/v1",
    "api_key":  lambda: config.GROQ_API_KEY,
    "model":    lambda: config.GROQ_MODEL,
},
```

**3. Add to your** `.env`:
```
GROQ_API_KEY=gsk_your_key
LLM_PROVIDERS=nvidia,groq,openrouter,ollama
```

Done. Groq is now in the fallback chain.

---

## The Local Fallback (Ollama)

Ollama is the "always available" option at the bottom of the chain. It runs models locally on your GPU (or CPU). No API key, no rate limits, no cost, no internet required.

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull a model
ollama pull llama3.3

# It runs as a local HTTP server on port 11434
# GawdBotE connects to http://localhost:11434/v1
```

The tradeoff: local models are slower and less capable than the big cloud models. But they're infinitely reliable for when the cloud is unavailable.

---

## Exercise

Open `.env` and change `LLM_PROVIDERS` to put `ollama` first:

```
LLM_PROVIDERS=ollama,nvidia,openrouter
```

Run `gawdbote chat` and ask a question. You'll see in the logs which provider answered. Then change it back to `nvidia,openrouter,ollama` and notice the difference in response quality and speed.

Then try something that will *force* a fallback: set `NVIDIA_API_KEY=invalid_key_here` temporarily and notice GawdBotE automatically falls back to OpenRouter without failing.

---

## Key Takeaways

- **Single provider = single point of failure.** Always have at least one fallback.
- OpenRouter is the ideal cloud backup — one key, 200+ models
- Ollama is the local fallback — no internet needed, always available
- The **adapter pattern** normalizes different API formats to one interface
- Adding a new provider takes ~10 lines of code
- The fallback logic is just a for-loop with try/except
