# Lesson 06 — Web Search

## Why Web Search Matters

LLMs have a **training cutoff** — they don't know anything that happened after they were trained. If you ask "who won the game last night?" or "what's the latest version of Python?", a plain LLM will either hallucinate or admit it doesn't know.

Web search solves this. The agent can look things up in real time, then use that information in its response.

---

## Two Search Options in GawdBotE

Configured via `WEB_SEARCH_PROVIDER` in `.env`:

| Provider | Cost | Notes |
|----------|------|-------|
| DuckDuckGo | Free, no key | Uses Instant Answer API |
| Brave | Paid, requires `BRAVE_API_KEY` | Higher quality, structured results |

Default is DuckDuckGo — free and works immediately with no setup.

---

## DuckDuckGo Instant Answer API

DuckDuckGo has a free, undocumented-but-stable API that returns JSON:

```
GET https://api.duckduckgo.com/?q=python+latest+version&format=json&no_html=1
```

The response includes:
- `AbstractText` — a summary (usually from Wikipedia)
- `AbstractURL` — source URL
- `RelatedTopics` — list of relevant topics with text and links

```python
async def _duckduckgo_search(query: str, max_results: int) -> str:
    import urllib.parse, aiohttp

    encoded = urllib.parse.quote_plus(query)
    url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json(content_type=None)

    results = []
    if data.get("AbstractText"):
        results.append(f"**Summary**: {data['AbstractText']}\nSource: {data.get('AbstractURL', '')}")

    for topic in data.get("RelatedTopics", [])[:max_results]:
        if isinstance(topic, dict) and topic.get("Text"):
            results.append(f"- {topic['Text']}\n  {topic.get('FirstURL', '')}")

    return f"Web search results for '{query}':\n\n" + "\n\n".join(results[:max_results])
```

Key things happening here:
1. `urllib.parse.quote_plus` — URL-encodes the query ("python latest version" → "python+latest+version")
2. `aiohttp.ClientSession` — async HTTP client (never use synchronous `requests` in async code)
3. `timeout=aiohttp.ClientTimeout(total=10)` — 10 second cap; never let a web request hang forever
4. `content_type=None` — DuckDuckGo doesn't set the right Content-Type header, so we skip validation

---

## Brave Search API

Brave returns higher quality structured results. The code is similar but cleaner:

```python
async def _brave_search(query: str, max_results: int) -> str:
    import aiohttp

    headers = {
        "Accept": "application/json",
        "X-Subscription-Token": config.BRAVE_API_KEY,
    }
    params = {"q": query, "count": max_results}

    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers=headers, params=params,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            data = await resp.json()

    results = []
    for item in data.get("web", {}).get("results", [])[:max_results]:
        results.append(
            f"**{item['title']}**\n{item.get('description', '')}\n{item['url']}"
        )

    return f"Web search results for '{query}':\n\n" + "\n\n".join(results)
```

---

## The Public Interface

Both search backends are wrapped in one function that the agent calls:

```python
async def search(query: str, max_results: int = None) -> str:
    max_results = max_results or config.WEB_SEARCH_MAX_RESULTS
    provider = config.WEB_SEARCH_PROVIDER

    try:
        if provider == "brave" and config.BRAVE_API_KEY:
            return await _brave_search(query, max_results)
        else:
            return await _duckduckgo_search(query, max_results)
    except Exception as e:
        return f"Web search failed: {e}"
```

The agent never needs to know which backend is being used. If search fails entirely, it returns an error string — the agent sees this and can tell the user search isn't available rather than crashing.

---

## How the Agent Uses Search

The agent decides to search when it needs current information. From the system prompt:

> "Use web search when you need current information you may not have"

When you ask "what's the weather in Austin?", the LLM reasons:
1. "I don't have real-time weather data"
2. "I have a `web_search` tool"
3. Calls `web_search(query="current weather Austin Texas")`
4. Gets back text with weather info
5. Formats a nice response

---

## aiohttp vs. requests

You might wonder why we use `aiohttp` instead of the popular `requests` library.

```python
# ❌ Never do this in async code
import requests
response = requests.get(url)  # BLOCKS the entire event loop

# ✅ Do this instead
import aiohttp
async with aiohttp.ClientSession() as session:
    async with session.get(url) as resp:
        data = await resp.json()
```

GawdBotE runs everything in an `asyncio` event loop. If one coroutine blocks (waits without yielding), *everything else* — all your Telegram messages, Discord replies, cron jobs — stops dead until it unblocks. `aiohttp` is the async-native HTTP client that never blocks.

---

## Exercise

Open `tools/web_search.py` and add a **news search** function using the same DuckDuckGo API but with `&t=news` appended:

```python
async def news_search(query: str, max_results: int = 5) -> str:
    encoded = urllib.parse.quote_plus(query)
    url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&t=news"
    # ... same as _duckduckgo_search ...
```

Then add a `news_search` tool to the agent. Ask "what's in the news today?" and see what happens.

---

## Key Takeaways

- Web search gives the agent **real-time information** beyond its training cutoff
- DuckDuckGo is free, no key needed — great default
- Brave is higher quality but requires an API key
- Always use **async HTTP** (`aiohttp`) in an async application — never `requests`
- Always set a **timeout** on web requests — never let them hang forever
- Wrap search in try/except and return an error string on failure — don't crash the agent
