"""
Discord bot interface — inspired by OpenClaw's Discord integration.
Commands: !evolve, !memory, !search
Responds to @mentions and DMs. Supports voice messages (attachments).
"""
from __future__ import annotations
import asyncio
import logging
from typing import Optional

import config
from core import agent, approvals, events, memory, security
from core.self_evolve import evolve

log = logging.getLogger(__name__)

PREFIX = "!"


async def run() -> None:
    if not config.DISCORD_ENABLED or not config.DISCORD_BOT_TOKEN:
        log.info("Discord bot disabled — set DISCORD_ENABLED=true and DISCORD_BOT_TOKEN")
        return

    if not config.DISCORD_ALLOWED_USERS:
        log.error(
            "Discord bot NOT started: no allowed users. Set DISCORD_ALLOWED_USER_IDS to your numeric "
            "Discord user ID (turn on Developer Mode, right click your name, Copy User ID). Without a "
            "guest list, anyone in a server with this bot could run commands on this computer."
        )
        return

    try:
        import discord
        from discord.ext import commands
    except ImportError:
        log.error("discord.py not installed. Run: pip install discord.py")
        return

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix=PREFIX, intents=intents)

    def _allowed(channel_id: int, user_id: int) -> bool:
        # Two checks: the right channel (if one is configured) AND an allowlisted person.
        channel_ok = config.DISCORD_CHANNEL_ID == 0 or channel_id == config.DISCORD_CHANNEL_ID
        return channel_ok and security.is_authorized(user_id, config.DISCORD_ALLOWED_USERS)

    @bot.event
    async def on_ready():
        log.info("Discord bot ready as %s", bot.user)

    def _asker_for(channel):
        """Build the function that shows approval requests in this Discord channel."""
        async def ask(req) -> None:
            text = req.prompt_text("!approve", "!deny")
            # Discord caps messages at 2000 characters. Send long prompts in pieces rather
            # than cutting them, so you always see the whole command you are approving.
            for i in range(0, len(text), 1900):
                await channel.send(text[i:i + 1900])
        return ask

    async def _answer(ctx, request_id: str, approved: bool) -> None:
        if not _allowed(ctx.channel.id, ctx.author.id):
            return
        if not request_id:
            await ctx.send(f"Usage: `!{'approve' if approved else 'deny'} <id>`")
            return
        await ctx.send(approvals.resolve(request_id, approved, by=f"discord:{ctx.author.id}", channel="discord"))

    @bot.command(name="approve")
    async def cmd_approve(ctx, request_id: str = ""):
        await _answer(ctx, request_id, True)

    @bot.command(name="deny")
    async def cmd_deny(ctx, request_id: str = ""):
        await _answer(ctx, request_id, False)

    @bot.command(name="trace")
    async def cmd_trace(ctx, session_id: str = ""):
        if not _allowed(ctx.channel.id, ctx.author.id):
            return
        sid = session_id or events.last_session_id("discord")
        await ctx.send((events.format_session(sid, max_chars=1990) if sid else "No runs logged yet."))

    @bot.command(name="evolve")
    async def cmd_evolve(ctx, *, request: str = ""):
        if not _allowed(ctx.channel.id, ctx.author.id):
            return
        if not request:
            await ctx.send("Usage: `!evolve <what to improve>`")
            return
        msg = await ctx.send("Starting self-improvement cycle…")
        result = await evolve(request, source="discord", ask=_asker_for(ctx.channel), channel="discord")
        await msg.edit(content=result[:2000])

    @bot.command(name="memory")
    async def cmd_memory(ctx, *, query: str = ""):
        if not _allowed(ctx.channel.id, ctx.author.id):
            return
        if not query:
            await ctx.send("Usage: `!memory <query>`")
            return
        results = await memory.search(query)
        if not results:
            await ctx.send("No memories found.")
            return
        lines = [f"[{r['score']:.2f}] {r['content'][:200]}" for r in results]
        await ctx.send("\n\n".join(lines)[:2000])

    @bot.command(name="search")
    async def cmd_search(ctx, *, query: str = ""):
        if not _allowed(ctx.channel.id, ctx.author.id):
            return
        if not query:
            await ctx.send("Usage: `!search <query>`")
            return
        from tools.web_search import search
        result = await search(query)
        await ctx.send(result[:2000])

    @bot.event
    async def on_message(message: discord.Message):
        if message.author.bot:
            return
        if not _allowed(message.channel.id, message.author.id):
            return

        # Process commands first
        await bot.process_commands(message)
        if message.content.startswith(PREFIX):
            return

        # Respond to @mentions or DMs
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mention = bot.user in message.mentions
        if not (is_dm or is_mention):
            return

        text = message.content.replace(f"<@{bot.user.id}>", "").strip()
        if not text:
            return

        async with message.channel.typing():
            response = await agent.run(text, source="discord", ask=_asker_for(message.channel), channel="discord")
        await message.reply(response[:2000])

    log.info("Discord bot starting")
    await bot.start(config.DISCORD_BOT_TOKEN)
