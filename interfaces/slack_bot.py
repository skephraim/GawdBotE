"""
Slack bot interface — Socket Mode (no public URL needed).
Requires: slack-bolt, SLACK_BOT_TOKEN (xoxb-...), SLACK_APP_TOKEN (xapp-...)
"""
from __future__ import annotations
import asyncio
import logging

import config
from core import agent, memory, security
from core.self_evolve import evolve

log = logging.getLogger(__name__)


async def run() -> None:
    if not config.SLACK_ENABLED or not config.SLACK_BOT_TOKEN or not config.SLACK_APP_TOKEN:
        log.info("Slack bot disabled — set SLACK_ENABLED=true, SLACK_BOT_TOKEN, SLACK_APP_TOKEN")
        return

    if not config.SLACK_ALLOWED_USERS:
        log.error(
            "Slack bot NOT started: no allowed users. Set SLACK_ALLOWED_USER_IDS to your Slack member ID "
            "(profile, three dots, Copy member ID). Without a guest list, anyone in the workspace "
            "could run commands on this computer."
        )
        return

    try:
        from slack_bolt.async_app import AsyncApp
        from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
    except ImportError:
        log.error("slack-bolt not installed. Run: pip install slack-bolt")
        return

    app = AsyncApp(token=config.SLACK_BOT_TOKEN)

    def _authorized(event) -> bool:
        return security.is_authorized(event.get("user", ""), config.SLACK_ALLOWED_USERS)

    @app.event("app_mention")
    async def handle_mention(event, say):
        if not _authorized(event):
            return
        text = event.get("text", "")
        # Strip mention
        import re
        text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()

        if text.startswith("evolve "):
            result = await evolve(text[7:], source="slack")
            await say(result[:3000])
        elif text.startswith("memory "):
            results = await memory.search(text[7:])
            if not results:
                await say("No memories found.")
            else:
                text = memory.format_results(results)
                await say(text[:3000])
        elif text.startswith("search "):
            from tools.web_search import search
            result = await search(text[7:])
            await say(result[:3000])
        else:
            response = await agent.run(text, source="slack")
            await say(response[:3000])

    @app.event("message")
    async def handle_dm(event, say):
        # Only handle DMs (channel_type == "im")
        if event.get("channel_type") != "im":
            return
        if event.get("bot_id"):
            return
        if not _authorized(event):
            return
        text = event.get("text", "").strip()
        if not text:
            return
        response = await agent.run(text, source="slack_dm")
        await say(response[:3000])

    log.info("Slack bot starting (Socket Mode)")
    handler = AsyncSocketModeHandler(app, config.SLACK_APP_TOKEN)
    await handler.start_async()
