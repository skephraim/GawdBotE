"""
HTTP webhook server — POST to /webhook to trigger the agent.
Endpoints:
  GET  /health
  POST /webhook          { "message": "..." }
  POST /webhook/embed    { "text": "..." } or { "texts": [...] }
  POST /webhook/evolve   { "request": "..." }
  POST /webhook/search   { "query": "..." }
"""
from __future__ import annotations
import json
import logging

import config
from core import agent, memory
from core.self_evolve import evolve

log = logging.getLogger(__name__)


async def run() -> None:
    if not config.WEBHOOK_ENABLED:
        log.info("Webhook server disabled")
        return

    try:
        from aiohttp import web
    except ImportError:
        log.error("aiohttp not installed. Run: pip install aiohttp")
        return

    def _auth(request) -> bool:
        secret = request.headers.get("X-Webhook-Secret", "")
        return secret == config.WEBHOOK_SECRET

    async def health(request):
        return web.json_response({"status": "ok", "service": "gawdbote"})

    async def webhook(request):
        if not _auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        message = body.get("message", "")
        if not message:
            return web.json_response({"error": "Missing 'message' field"}, status=400)

        response = await agent.run(message, source="webhook")
        return web.json_response({"response": response})

    async def webhook_embed(request):
        if not _auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        if "texts" in body:
            import asyncio
            embeddings = await asyncio.gather(*[memory._embed(t) for t in body["texts"]])
            return web.json_response({"embeddings": embeddings})
        elif "text" in body:
            emb = await memory._embed(body["text"])
            return web.json_response({"embedding": emb})
        else:
            return web.json_response({"error": "Missing 'text' or 'texts'"}, status=400)

    async def webhook_evolve(request):
        if not _auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        req = body.get("request", "")
        if not req:
            return web.json_response({"error": "Missing 'request' field"}, status=400)
        result = await evolve(req, source="webhook")
        return web.json_response({"result": result})

    async def webhook_search(request):
        if not _auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        query = body.get("query", "")
        if not query:
            return web.json_response({"error": "Missing 'query' field"}, status=400)
        from tools.web_search import search
        result = await search(query, max_results=body.get("max_results", 5))
        return web.json_response({"result": result})

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_post("/webhook", webhook)
    app.router.add_post("/webhook/embed", webhook_embed)
    app.router.add_post("/webhook/evolve", webhook_evolve)
    app.router.add_post("/webhook/search", webhook_search)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.WEBHOOK_HOST, config.WEBHOOK_PORT)
    await site.start()
    log.info("Webhook server listening on %s:%d", config.WEBHOOK_HOST, config.WEBHOOK_PORT)

    import asyncio
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
