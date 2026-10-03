"""
Telegram bot interface.
Commands: /start, /help, /evolve, /memory, /search
Voice messages are transcribed and handled as text.
"""
from __future__ import annotations
import asyncio
import logging

import config
from core import agent, approvals, events, memory, security, voice
from core.self_evolve import evolve

log = logging.getLogger(__name__)


async def run() -> None:
    if not config.TELEGRAM_BOT_TOKEN:
        log.info("Telegram bot token not set — interface disabled")
        return

    if not config.TELEGRAM_ALLOWED_USERS:
        log.error(
            "Telegram bot NOT started: no allowed users. Set TELEGRAM_ALLOWED_USER_IDS to your numeric "
            "Telegram user ID (message @userinfobot to find it). An empty list now means nobody, "
            "because anyone who can message the bot could otherwise control this computer."
        )
        return

    try:
        from telegram import Update
        from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
    except ImportError:
        log.error("python-telegram-bot not installed. Run: pip install python-telegram-bot")
        return

    def _authorized(user_id: int) -> bool:
        return security.is_authorized(user_id, config.TELEGRAM_ALLOWED_USERS)

    def _asker_for(chat_id: int):
        """Build the function that shows approval requests in this chat."""
        async def ask(req) -> None:
            await app.bot.send_message(chat_id=chat_id, text=req.prompt_text("/approve", "/deny"))
        return ask

    async def _owner_ask(req) -> None:
        """Approvals for unattended runs (cron, webhook, voice) go to every allowlisted owner's private chat."""
        sent = 0
        for uid in config.TELEGRAM_ALLOWED_USERS:
            if uid.lstrip("-").isdigit():
                try:
                    await app.bot.send_message(chat_id=int(uid), text=req.prompt_text("/approve", "/deny"))
                    sent += 1
                except Exception as exc:   # the owner may never have pressed Start on the bot
                    log.warning("Could not send approval to Telegram user %s: %s", uid, exc)
        if not sent:
            raise RuntimeError("no allowlisted Telegram user could be reached")

    async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update.effective_user.id):
            return
        await update.message.reply_text(
            "GawdBotE online. I have full system access, voice control, web search, and self-improvement.\n"
            "Use /help to see commands."
        )

    async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update.effective_user.id):
            return
        await update.message.reply_text(
            "/evolve <request> — trigger self-improvement\n"
            "/memory <query> — search persistent memory\n"
            "/search <query> — web search\n"
            "/approve <id> or /deny <id> — answer an approval request\n"
            "/trace — step by step log of the last run\n"
            "Any message — chat with GawdBotE\n"
            "Voice message — transcribed and handled as text"
        )

    async def _answer(update: Update, ctx: ContextTypes.DEFAULT_TYPE, approved: bool) -> None:
        if not _authorized(update.effective_user.id):
            return
        if not ctx.args:
            await update.message.reply_text(f"Usage: /{'approve' if approved else 'deny'} <id>")
            return
        msg = approvals.resolve(ctx.args[0], approved, by=f"telegram:{update.effective_user.id}", channel="telegram")
        await update.message.reply_text(msg)

    async def cmd_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await _answer(update, ctx, True)

    async def cmd_deny(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await _answer(update, ctx, False)

    async def cmd_trace(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update.effective_user.id):
            return
        sid = ctx.args[0] if ctx.args else events.last_session_id("telegram")
        await update.message.reply_text(events.format_session(sid) if sid else "No runs logged yet.")

    async def cmd_evolve(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _authorized(update.effective_user.id):
            return
        request = " ".join(ctx.args)
        if not request:
            await update.message.reply_text("Usage: /evolve <what to improve>")
            return
        await update.message.reply_text("Starting self-improvement cycle…")
        result = await evolve(request, source="telegram", ask=_asker_for(update.effective_chat.id), channel="telegram")
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
        response = await agent.run(text, source="telegram", ask=_asker_for(update.effective_chat.id), channel="telegram")
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
        response = await agent.run(text, source="telegram_voice", ask=_asker_for(update.effective_chat.id), channel="telegram")
        await update.message.reply_text(response[:4000])

    # concurrent_updates(True) is REQUIRED for approvals. By default Telegram updates are
    # handled one at a time, so a run waiting for /approve would block the /approve message
    # itself, and both would wait forever (a deadlock).
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).concurrent_updates(True).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("deny", cmd_deny))
    app.add_handler(CommandHandler("trace", cmd_trace))
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
    approvals.register_owner_channel("telegram", _owner_ask)

    # Keep running until cancelled
    try:
        await asyncio.Event().wait()
    finally:
        approvals.clear_owner_channel()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
