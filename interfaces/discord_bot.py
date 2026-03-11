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
from core import agent, memory
from core.self_evolve import evolve

log = logging.getLogger(__name__)

PREFIX = "!"


async def run() -> None:
    if not config.DISCORD_ENABLED or not config.DISCORD_BOT_TOKEN:
        log.info("Discord bot disabled — set DISCORD_ENABLED=true and DISCORD_BOT_TOKEN")
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

    def _allowed(channel_id: int) -> bool:
        return config.DISCORD_CHANNEL_ID == 0 or channel_id == config.DISCORD_CHANNEL_ID

    @bot.event
    async def on_ready():
        log.info("Discord bot ready as %s", bot.user)

    @bot.command(name="evolve")
    async def cmd_evolve(ctx, *, request: str = ""):
        if not _allowed(ctx.channel.id):
            return
        if not request:
            await ctx.send("Usage: `!evolve <what to improve>`")
            return
        msg = await ctx.send("Starting self-improvement cycle…")
        result = await evolve(request, source="discord")
        await msg.edit(content=result[:2000])

    @bot.command(name="memory")
    async def cmd_memory(ctx, *, query: str = ""):
        if not _allowed(ctx.channel.id):
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
        if not _allowed(ctx.channel.id):
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
        if not _allowed(message.channel.id):
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
            response = await agent.run(text, source="discord")
        await message.reply(response[:2000])

    log.info("Discord bot starting")
    await bot.start(config.DISCORD_BOT_TOKEN)
