# Lesson 09 — Interfaces

## One Agent, Many Doors

GawdBotE has a single brain (`core/agent.py`) but multiple ways to reach it:

```
Telegram  ──┐
Discord   ──┤
Slack     ──┼──► agent.run(message, source=...) ──► response string
Webhooks  ──┤
Voice     ──┘
```

Every interface calls the same `agent.run()` function. The agent doesn't care where the message came from — it just thinks and responds. This is the **separation of concerns** principle: the interfaces handle communication, the agent handles reasoning.

---

## How It All Runs Concurrently

In `main.py`, all interfaces start simultaneously using `asyncio.gather`:

```python
async def main():
    tasks = []

    if config.TELEGRAM_BOT_TOKEN:
        tasks.append(asyncio.create_task(telegram_bot.run(), name="telegram"))

    if config.DISCORD_ENABLED:
        tasks.append(asyncio.create_task(discord_bot.run(), name="discord"))

    if config.SLACK_ENABLED:
        tasks.append(asyncio.create_task(slack_bot.run(), name="slack"))

    if config.WEBHOOK_ENABLED:
        tasks.append(asyncio.create_task(webhook_server.run(), name="webhook"))

    await asyncio.gather(*tasks)   # run all interfaces at the same time
```

`asyncio.gather` runs all coroutines concurrently in a single thread. While one interface is waiting for a message, others are processing. This is much more efficient than running each in a separate thread.

---

## Telegram Bot

Telegram is the primary interface — the most full-featured one.

```python
# From interfaces/telegram_bot.py

app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

# Command handlers
app.add_handler(CommandHandler("evolve",  cmd_evolve))
app.add_handler(CommandHandler("memory",  cmd_memory))
app.add_handler(CommandHandler("search",  cmd_search))

# Message handlers
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
app.add_handler(MessageHandler(filters.VOICE, handle_voice))  # voice messages!
```

The library `python-telegram-bot` handles the long-polling loop — it keeps a connection open to Telegram's servers and fires your handlers when messages arrive.

**Voice messages in Telegram** are particularly interesting:

```python
async def handle_voice(update, ctx):
    # 1. Download the .ogg audio file from Telegram
    voice_file = await update.message.voice.get_file()
    ogg_bytes = await voice_file.download_as_bytearray()

    # 2. Convert OGG → WAV with ffmpeg (Whisper needs WAV)
    subprocess.run(["ffmpeg", "-y", "-i", ogg_path, wav_path], capture_output=True)

    # 3. Transcribe with Whisper
    text = voice.transcribe(wav_path)

    # 4. Send to agent just like a text message
    response = await agent.run(text, source="telegram_voice")
    await update.message.reply_text(response)
```

Telegram compresses voice messages to OGG format. We convert to WAV because that's what Whisper expects, transcribe to text, then pass to the agent normally.

**Security** — only your Telegram user ID can talk to it:

```python
def _authorized(user_id: int) -> bool:
    return config.TELEGRAM_USER_ID == 0 or user_id == config.TELEGRAM_USER_ID
```

Set `TELEGRAM_USER_ID=0` to allow anyone (not recommended).

---

## Discord Bot

Discord uses a similar pattern via `discord.py`:

```python
# From interfaces/discord_bot.py

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return   # ignore other bots

    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mention = bot.user in message.mentions

    if not (is_dm or is_mention):
        return   # only respond when @mentioned or in DMs

    text = message.content.replace(f"<@{bot.user.id}>", "").strip()

    async with message.channel.typing():   # show "GawdBotE is typing..."
        response = await agent.run(text, source="discord")

    await message.reply(response[:2000])   # Discord has 2000 char limit
```

The `async with message.channel.typing()` is a nice UX touch — Discord shows the typing indicator while the agent is thinking.

Discord also has **slash commands** (`!evolve`, `!memory`, `!search`) using the `commands.Bot` framework.

---

## Webhook Server

The webhook is an HTTP API — useful for automation, other apps, or testing from the terminal:

```python
# From interfaces/webhook_server.py
# Built with aiohttp (async web framework)

async def webhook(request):
    if not _auth(request):   # check X-Webhook-Secret header
        return web.json_response({"error": "Unauthorized"}, status=401)

    body = await request.json()
    message = body.get("message", "")
    response = await agent.run(message, source="webhook")
    return web.json_response({"response": response})
```

Four endpoints:
```
GET  /health              → {"status": "ok"}
POST /webhook             → {"message": "..."} → {"response": "..."}
POST /webhook/embed       → {"text": "..."}   → {"embedding": [...]}
POST /webhook/evolve      → {"request": "..."} → {"result": "..."}
POST /webhook/search      → {"query": "..."}   → {"result": "..."}
```

Call it from the terminal:
```bash
curl -X POST http://localhost:8080/webhook \
  -H "X-Webhook-Secret: your-secret" \
  -H "Content-Type: application/json" \
  -d '{"message": "what is 2+2?"}'
```

---

## The Importance of Rate Limiting (What's Missing)

GawdBotE doesn't currently have rate limiting — if someone sends 100 messages in a second, the agent will try to process all 100. For a personal assistant, this is fine. For a public-facing bot, you'd add:

```python
# Simple per-user rate limiting example
from collections import defaultdict
import time

_last_call = defaultdict(float)

def _rate_limited(user_id: int, min_seconds=2) -> bool:
    now = time.time()
    if now - _last_call[user_id] < min_seconds:
        return True
    _last_call[user_id] = now
    return False
```

This is a good self-evolution request: "add rate limiting to the Telegram bot."

---

## Adding a New Interface

The pattern is always the same:

1. Create `interfaces/myplatform.py` with `async def run():`
2. Inside, set up whatever event loop or polling the platform needs
3. When a message arrives, call `await agent.run(text, source="myplatform")`
4. Send the returned string back to the platform
5. Add to `main.py`: `tasks.append(asyncio.create_task(myplatform.run()))`

Example skeleton:
```python
async def run():
    # Connect to platform
    # Set up event loop / polling
    while True:
        message = await platform.get_next_message()
        response = await agent.run(message.text, source="myplatform")
        await platform.send(message.reply_to, response)
```

---

## Exercise

Look at `interfaces/webhook_server.py`. Add a new endpoint `/webhook/memory` that:
- Takes `{"query": "..."}` in the request body
- Returns the top 3 matching memories as JSON
- Uses `memory.search(query)`

Hint: Copy the pattern from `/webhook/search`.

---

## Key Takeaways

- All interfaces call the same `agent.run()` function — the agent doesn't know or care about the delivery mechanism
- `asyncio.gather` runs all interfaces concurrently in one thread
- Telegram: full-featured, handles voice messages, secured by user ID
- Discord: responds to @mentions and DMs, shows typing indicator
- Slack: Socket Mode (no public URL needed), handles DMs and @mentions
- Webhooks: HTTP API for automation and testing
- Adding a new interface is ~50 lines of code following a standard pattern
