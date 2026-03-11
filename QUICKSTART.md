# GawdBotE — Quick Start Guide

Get up and running in under 15 minutes.

---

## What You Need Before Starting

| Requirement | Why |
|---|---|
| Linux (Ubuntu 22.04+ recommended) | systemd + voice stack |
| Python 3.11 or newer | `python3 --version` to check |
| Git | `sudo apt install git` |
| An NVIDIA NIM API key **or** OpenRouter key | Free at build.nvidia.com or openrouter.ai |
| A Telegram bot token *(optional but recommended)* | Talk to @BotFather on Telegram |

> **Minimum to get started:** Just an API key from one LLM provider. Everything else is optional.

---

## Step 1 — Get the Code

```bash
git clone https://github.com/skephraim/GawdBotE.git
cd GawdBotE
```

---

## Step 2 — Run the Installer

The installer does three things: sets up the Python environment, installs the systemd service, and creates the `gawdbote` CLI command.

```bash
chmod +x install.sh
./install.sh
```

You should see:
```
✓ Virtual environment created
✓ Dependencies installed
✓ systemd service installed
✓ gawdbote CLI installed to ~/.local/bin/gawdbote
```

> If `gawdbote` isn't found after install, run: `source ~/.bashrc`

---

## Step 3 — Configure Your API Keys

Copy the example config file and open it in a text editor:

```bash
cp .env.example .env
nano .env
```

### Minimum configuration (pick ONE LLM provider):

**Option A — NVIDIA NIM (free tier, recommended)**
```bash
# Get your key at: build.nvidia.com → API Keys
NVIDIA_API_KEY=nvapi-your-key-here
LLM_PROVIDERS=nvidia,openrouter,ollama
```

**Option B — OpenRouter (200+ models, pay-per-use)**
```bash
# Get your key at: openrouter.ai/keys
OPENROUTER_API_KEY=sk-or-your-key-here
LLM_PROVIDERS=openrouter,ollama
```

**Option C — Ollama (fully local, no key needed)**
```bash
# Install Ollama first: curl -fsSL https://ollama.com/install.sh | sh
# Then: ollama pull llama3.3
LLM_PROVIDERS=ollama
```

### Add Telegram (highly recommended — best interface):

1. Open Telegram → search for **@BotFather**
2. Send `/newbot` → follow prompts → copy the token
3. Find your user ID: message **@userinfobot**

```bash
TELEGRAM_BOT_TOKEN=your-bot-token-here
TELEGRAM_USER_ID=123456789
```

### Disable features you don't need yet:

```bash
VOICE_ENABLED=false       # enable later after installing Piper + Whisper
DISCORD_ENABLED=false     # enable if you want a Discord bot
SLACK_ENABLED=false       # enable if you want a Slack bot
PC_CONTROL_ENABLED=true   # keep true for screenshots and mouse control
```

Save and close: `Ctrl+O`, `Enter`, `Ctrl+X`

---

## Step 4 — Health Check

Verify everything is configured correctly:

```bash
gawdbote doctor
```

Sample output:
```
✓ Python 3.11.9
✓ .env loaded
✓ aiohttp available
✓ LLM configured: nvidia
✓ Telegram configured
~ Discord disabled (DISCORD_ENABLED=false)
~ Voice disabled (VOICE_ENABLED=false)
✓ Web search: duckduckgo
✓ Git configured
```

A `~` means the feature is disabled (not an error). Fix any `✗` items before continuing.

---

## Step 5 — First Run (interactive test)

Before running as a background service, test it interactively:

```bash
gawdbote chat
```

You'll get a prompt. Type a message:
```
You: What time is it?
GawdBotE: The current time is 2:47 PM.

You: Search the web for Python 3.14 news
GawdBotE: Here's what I found...

You: exit
```

If it responds, everything is working.

---

## Step 6 — Run as a Persistent Background Service

Start GawdBotE as a systemd service (auto-starts on login, auto-restarts on crash):

```bash
gawdbote start
gawdbote status    # verify it's running
gawdbote logs      # watch live output (Ctrl+C to stop watching)
```

Now open Telegram and message your bot. It should respond.

---

## Day-to-Day Commands

```bash
gawdbote start      # start the service
gawdbote stop       # stop the service
gawdbote restart    # restart after config changes
gawdbote status     # is it running?
gawdbote logs       # live logs (Ctrl+C to exit)
gawdbote chat       # interactive terminal session
gawdbote doctor     # health check
gawdbote backup create   # create a backup archive
```

### From Telegram:
```
/help              — list commands
/memory python     — search your memory for "python"
/search news       — web search
/evolve add a tool that tells me my public IP
```

### From the terminal:
```bash
gawdbote ask "what's the weather like in London?"
gawdbote evolve "add a brightness control tool"
```

---

## Optional: Enable Voice

Voice lets you say "Hey Jarvis" and talk to GawdBotE out loud.

**Install system dependencies:**
```bash
sudo apt install python3-pyaudio portaudio19-dev ffmpeg
pip install faster-whisper openwakeword
```

**Install Piper TTS:**
```bash
# Download the Piper binary from: github.com/rhasspy/piper/releases
# Then download a voice model:
mkdir -p ~/.local/share/piper
wget -P ~/.local/share/piper \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/northern_english_male/medium/en_GB-northern_english_male-medium.onnx \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/northern_english_male/medium/en_GB-northern_english_male-medium.onnx.json
```

**Enable in .env:**
```bash
VOICE_ENABLED=true
WAKE_WORD=hey jarvis
WHISPER_MODEL=base      # tiny=fastest, large=most accurate
WHISPER_DEVICE=cpu      # or cuda if you have a GPU
PIPER_MODEL=/home/YOU/.local/share/piper/en_GB-northern_english_male-medium.onnx
```

```bash
gawdbote restart
```

Say "Hey Jarvis, what's today's news?" — it should respond out loud.

---

## Optional: Enable Discord

1. Go to [discord.com/developers](https://discord.com/developers/applications) → New Application
2. Bot tab → Add Bot → copy the token
3. Enable **Message Content Intent** under Privileged Gateway Intents
4. OAuth2 → URL Generator → select `bot` scope + `Send Messages` permission → invite to your server

```bash
DISCORD_ENABLED=true
DISCORD_BOT_TOKEN=your-bot-token
```

```bash
gawdbote restart
```

In Discord, @mention the bot or DM it.

---

## Optional: Add Cron Jobs (Scheduled Tasks)

In `.env`:
```bash
CRON_JOBS=[
  {"schedule": "0 9 * * *", "message": "Good morning! Summarize my GitHub issues."},
  {"schedule": "0 18 * * 5", "message": "It's Friday — create a weekly summary."}
]
```

```bash
gawdbote restart
```

The agent will wake up on schedule and run those prompts automatically.

---

## Optional: Enable Self-Evolution (Agent Edits Its Own Code)

This lets GawdBotE open GitHub pull requests to improve itself.

1. Go to [github.com/settings/tokens](https://github.com/settings/tokens) → Generate new token (classic)
   - Scopes: `repo` (full)
2. Fork the repo to your GitHub account

```bash
GITHUB_TOKEN=ghp-your-token-here
GITHUB_REPO=yourusername/GawdBotE
SELF_EVOLVE_AUTO_MERGE=false   # review PRs before merging
```

Then try:
```bash
gawdbote evolve "add a tool that counts words in a text string"
```

Watch it read the source, make the change, and open a PR on GitHub.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `gawdbote: command not found` | Run `source ~/.bashrc` or restart terminal |
| Telegram bot doesn't respond | Check `gawdbote logs` for errors; verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_USER_ID` |
| LLM errors | Run `gawdbote doctor` to check provider config |
| Voice not working | Check `PIPER_MODEL` path is correct; test with `piper --help` |
| Service won't start | Check `gawdbote logs` for the error; common cause is missing API key |
| Port 8080 in use | Change `WEBHOOK_PORT=8081` in `.env` and restart |

---

## What's Next

- Read the **13-lesson course** in `course/` to understand how everything works
- Install new skills: `gawdbote ask "install the github skill from clawhub"`
- Build a new interface (SMS, email) — see Lesson 13 for the pattern
- Try a self-evolution request and review the PR it creates

---

*Built by combining GawdBot + OpenClaw. See course/README.md to learn how it all works.*
