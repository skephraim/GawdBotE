"""
SuperAI — entry point.
Starts all enabled interfaces concurrently:
  - Telegram bot
  - Discord bot
  - Slack bot
  - Webhook HTTP server
  - Voice wake-word listener
  - Cron scheduler
"""
from __future__ import annotations
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("superai")


async def main() -> None:
    log.info("SuperAI starting up…")

    import config
    from interfaces import telegram_bot, discord_bot, slack_bot, webhook_server
    from scheduler import cron
    from core import voice, agent

    tasks = []

    # ── Telegram ──────────────────────────────────────────────────────────────
    if config.TELEGRAM_BOT_TOKEN:
        tasks.append(asyncio.create_task(telegram_bot.run(), name="telegram"))
        log.info("Telegram bot enabled")

    # ── Discord ───────────────────────────────────────────────────────────────
    if config.DISCORD_ENABLED and config.DISCORD_BOT_TOKEN:
        tasks.append(asyncio.create_task(discord_bot.run(), name="discord"))
        log.info("Discord bot enabled")

    # ── Slack ─────────────────────────────────────────────────────────────────
    if config.SLACK_ENABLED and config.SLACK_BOT_TOKEN:
        tasks.append(asyncio.create_task(slack_bot.run(), name="slack"))
        log.info("Slack bot enabled")

    # ── Webhook server ────────────────────────────────────────────────────────
    if config.WEBHOOK_ENABLED:
        tasks.append(asyncio.create_task(webhook_server.run(), name="webhook"))
        log.info("Webhook server enabled on port %d", config.WEBHOOK_PORT)

    # ── Cron scheduler ────────────────────────────────────────────────────────
    if config.CRON_ENABLED:
        tasks.append(asyncio.create_task(cron.run(), name="cron"))

    # ── Voice listener ────────────────────────────────────────────────────────
    if config.VOICE_ENABLED:
        def voice_callback(text: str) -> None:
            asyncio.create_task(_handle_voice(text))

        tasks.append(asyncio.create_task(
            voice.listen_for_wake_word(voice_callback), name="voice"
        ))
        log.info("Voice listener enabled (wake word: %r)", config.WAKE_WORD)

    if not tasks:
        log.warning("No interfaces enabled! Check your .env configuration.")
        log.info("Run: python -m cli.doctor to diagnose")
        return

    log.info("SuperAI running with %d active interface(s). Press Ctrl+C to stop.", len(tasks))

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        pass
    finally:
        log.info("SuperAI shutting down…")
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _handle_voice(text: str) -> None:
    """Handle a voice-transcribed message through the agent and speak the response."""
    from core import agent, voice as voice_mod
    log.info("Voice input: %r", text)
    response = await agent.run(text, source="voice")
    voice_mod.speak(response)


if __name__ == "__main__":
    # Allow CLI subcommands
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "doctor":
            from cli.doctor import main as doctor_main
            doctor_main(fix="--fix" in sys.argv)
        elif cmd == "backup":
            from cli.backup import main as backup_main
            backup_main()
        elif cmd == "chat":
            # Interactive CLI chat
            async def cli_chat():
                from core import agent
                print("SuperAI interactive chat (Ctrl+C to exit)\n")
                history = []
                while True:
                    try:
                        user_input = input("You: ").strip()
                    except (EOFError, KeyboardInterrupt):
                        print("\nBye!")
                        break
                    if not user_input:
                        continue
                    response = await agent.run(user_input, history=history, source="cli")
                    print(f"SuperAI: {response}\n")
                    history.append({"role": "user", "content": user_input})
                    history.append({"role": "assistant", "content": response})
            asyncio.run(cli_chat())
        else:
            print(f"Unknown command: {cmd}")
            print("Commands: doctor, backup, chat")
    else:
        asyncio.run(main())
