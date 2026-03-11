"""
Web search tool — DuckDuckGo (free, no key) or Brave Search API.
Inspired by OpenClaw's web search integration.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Optional

import config

log = logging.getLogger(__name__)


async def search(query: str, max_results: int = None) -> str:
    """Search the web and return formatted results."""
    max_results = max_results or config.WEB_SEARCH_MAX_RESULTS
    provider = config.WEB_SEARCH_PROVIDER

    try:
        if provider == "brave" and config.BRAVE_API_KEY:
            return await _brave_search(query, max_results)
        else:
            return await _duckduckgo_search(query, max_results)
    except Exception as e:
        log.warning("Web search error (%s): %s", provider, e)
        return f"Web search failed: {e}"


async def _duckduckgo_search(query: str, max_results: int) -> str:
    """DuckDuckGo Instant Answer API — free, no key required."""
    import urllib.parse
    import aiohttp

    encoded = urllib.parse.quote_plus(query)
    url = f"https://api.duckduckgo.com/?q={encoded}&format=json&no_html=1&skip_disambig=1"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json(content_type=None)

    results = []

    # Instant answer
    if data.get("AbstractText"):
        results.append(f"**Summary**: {data['AbstractText']}\nSource: {data.get('AbstractURL', '')}")

    # Related topics
    for topic in data.get("RelatedTopics", [])[:max_results]:
        if isinstance(topic, dict) and topic.get("Text"):
            results.append(f"- {topic['Text']}\n  {topic.get('FirstURL', '')}")
        elif isinstance(topic, dict) and topic.get("Topics"):
            for sub in topic["Topics"][:2]:
                if sub.get("Text"):
                    results.append(f"- {sub['Text']}\n  {sub.get('FirstURL', '')}")

    if not results:
        return f"No results found for: {query}"

    return f"Web search results for '{query}':\n\n" + "\n\n".join(results[:max_results])


async def _brave_search(query: str, max_results: int) -> str:
    """Brave Search API — requires BRAVE_API_KEY."""
    import aiohttp
    import urllib.parse

    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": config.BRAVE_API_KEY,
    }
    params = {"q": query, "count": max_results, "safesearch": "moderate"}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params,
                               timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json()

    results = []
    for item in data.get("web", {}).get("results", [])[:max_results]:
        results.append(
            f"**{item.get('title', 'No title')}**\n"
            f"{item.get('description', '')}\n"
            f"{item.get('url', '')}"
        )

    if not results:
        return f"No results found for: {query}"

    return f"Web search results for '{query}':\n\n" + "\n\n".join(results)
