"""
Telegram bot interface.
Commands: /start, /help, /evolve, /memory, /search
Voice messages are transcribed and handled as text.
"""
from __future__ import annotations
import asyncio
import logging

import config
from core import agent, memory, voice
from core.self_evolve import evolve

log = logging.getLogger(__name__)


async def run() -> None:
    if not config.TELEGRAM_BOT_TOKEN:
        log.info("Telegram bot token not set — interface disabled")
        return

    try:
        from telegram import Update
        from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
    except ImportError:
        log.error("python-telegram-bot not installed. Run: pip install python-telegram-bot")
        return

    def _authorized(user_id: int) -> bool:
        return config.TELEGRAM_USER_ID == 0 or user_id == config.TELEGRAM_USER_ID

    async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "GawdBotE online. I have full system access, voice control, web search, and self-improvement.\n"
            "Use /help to see commands."
        )

    async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "/evolve <request> — trigger self-improvement\n"
            "/memory <query> — search persistent memory\n"
            "/search <query> — web search\n"
            "Any message — chat with GawdBotE\n"
            "Voice message — transcribed and handled as text"
        )

    async def cmd_evolve(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update.effective_user.id):
            return
        request = " ".join(ctx.args)
        if not request:
            await update.message.reply_text("Usage: /evolve <what to improve>")
            return
        await update.message.reply_text("Starting self-improvement cycle…")
        result = await evolve(request, source="telegram")
        await update.message.reply_text(result[:4000])

    async def cmd_memory(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update.effective_user.id):
            return
        query = " ".join(ctx.args)
        if not query:
            await update.message.reply_text("Usage: /memory <query>")
            return
        results = await memory.search(query)
        if not results:
            await update.message.reply_text("No memories found.")
            return
        lines = [f"[{r['score']:.2f}] {r['content'][:200]}" for r in results]
        await update.message.reply_text("\n\n".join(lines)[:4000])

    async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update.effective_user.id):
            return
        query = " ".join(ctx.args)
        if not query:
            await update.message.reply_text("Usage: /search <query>")
            return
        from tools.web_search import search
        result = await search(query)
        await update.message.reply_text(result[:4000])

    async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update.effective_user.id):
            return
        text = update.message.text
        await update.message.chat.send_action("typing")
        response = await agent.run(text, source="telegram")
        await update.message.reply_text(response[:4000])

    async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update.effective_user.id):
            return
        voice_file = await update.message.voice.get_file()
        ogg_bytes = await voice_file.download_as_bytearray()

        # Convert OGG → WAV with ffmpeg
        import subprocess, tempfile, pathlib
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(ogg_bytes)
            ogg_path = f.name
        wav_path = ogg_path.replace(".ogg", ".wav")
        subprocess.run(["ffmpeg", "-y", "-i", ogg_path, wav_path],
                       capture_output=True)
        pathlib.Path(ogg_path).unlink(missing_ok=True)

        text = voice.transcribe(wav_path)
        pathlib.Path(wav_path).unlink(missing_ok=True)

        if not text:
            await update.message.reply_text("Could not transcribe voice message.")
            return

        await update.message.reply_text(f"Transcribed: {text}")
        await update.message.chat.send_action("typing")
        response = await agent.run(text, source="telegram_voice")
        await update.message.reply_text(response[:4000])

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("evolve", cmd_evolve))
    app.add_handler(CommandHandler("memory", cmd_memory))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    log.info("Telegram bot starting")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    log.info("Telegram bot running")

    # Keep running until cancelled
    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
