"""
GawdBotE — unified configuration.
All settings come from environment variables / .env file.
"""
from __future__ import annotations
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Project root ───────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent


# ── Helpers ────────────────────────────────────────────────────────────────────
def _secret(name: str, default: str = "") -> str:
    """
    Read a secret. The OS vault wins, then the environment / .env file.
    Run `python main.py secrets migrate` to move keys from .env into the vault.
    """
    try:
        from core import vault
        value = vault.get_secret(name)
        if value:
            return value
    except Exception:
        pass
    return os.getenv(name, default)


def _id_set(*names: str) -> set[str]:
    """
    Parse comma separated ID lists from the environment into a set of strings.
    An empty or missing variable gives an empty set, and an empty allowlist means
    NOBODY is allowed. A lone "0" is ignored so old "0 = allow all" settings fail closed.
    """
    found: set[str] = set()
    for name in names:
        for part in os.getenv(name, "").replace(";", ",").split(","):
            part = part.strip()
            if part and part != "0":
                found.add(part)
    return found

# ── LLM providers (tried in order for fallback) ────────────────────────────────
# Comma-separated list: nvidia,openrouter,ollama,openai,anthropic
# OpenRouter is the default cloud backup — it can route to almost any model
LLM_PROVIDERS = [p.strip() for p in os.getenv("LLM_PROVIDERS", "nvidia,openrouter,ollama").split(",")]

# NVIDIA NIM
NVIDIA_API_KEY = _secret("NVIDIA_API_KEY")
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
NVIDIA_VISION_MODEL = os.getenv("NVIDIA_VISION_MODEL", "meta/llama-3.2-11b-vision-instruct")

# Ollama (local)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.3")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "llava")

# OpenAI
OPENAI_API_KEY = _secret("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# Anthropic
ANTHROPIC_API_KEY = _secret("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-6")

# OpenRouter
OPENROUTER_API_KEY = _secret("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# ── Brains (see brains.example.toml) ───────────────────────────────────────────
# If brains.toml exists it decides which models run and in what order. If not, the
# provider settings above are used, exactly as before.
BRAINS_FILE = os.getenv("BRAINS_FILE", str(PROJECT_ROOT / "brains.toml"))
BRAIN_STATE_FILE = os.getenv("BRAIN_STATE_FILE", str(PROJECT_ROOT / "data" / "brain_state.json"))

# ── Voice ──────────────────────────────────────────────────────────────────────
WAKE_WORD = os.getenv("WAKE_WORD", "hey jarvis")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")   # or "cpu"
PIPER_MODEL = os.getenv(
    "PIPER_MODEL",
    str(Path.home() / ".local/share/piper/en_GB-northern_english_male-medium.onnx"),
)
VOICE_ENABLED = os.getenv("VOICE_ENABLED", "true").lower() == "true"

# ── Memory ─────────────────────────────────────────────────────────────────────
MEMORY_DB = os.getenv("MEMORY_DB", str(PROJECT_ROOT / "data" / "memory.db"))
MEMORY_MAX_RESULTS = int(os.getenv("MEMORY_MAX_RESULTS", "5"))
# How many relevant memories are handed to the agent at the start of every run (0 = off).
MEMORY_RECALL_K = int(os.getenv("MEMORY_RECALL_K", "4"))
# Raw chat exchanges are kept this many days. Facts, preferences, decisions, deadlines and
# tasks are kept until you delete them.
MEMORY_EXCHANGE_DAYS = int(os.getenv("MEMORY_EXCHANGE_DAYS", "90"))
# Embedding every chat exchange costs an API call per message. Off by default: exchanges are
# still found by keyword search. Saved facts are always embedded when an embedder is available.
MEMORY_EMBED_EXCHANGES = os.getenv("MEMORY_EMBED_EXCHANGES", "false").lower() == "true"

# ── Interfaces ─────────────────────────────────────────────────────────────────
# SECURITY: every chat interface gives the sender the power to run the agent, which
# can run shell commands. So each one needs an allowlist, and an EMPTY allowlist
# means nobody gets in. Use comma separated lists, e.g. TELEGRAM_ALLOWED_USER_IDS=111,222
TELEGRAM_BOT_TOKEN = _secret("TELEGRAM_BOT_TOKEN")
TELEGRAM_ALLOWED_USERS = _id_set("TELEGRAM_ALLOWED_USER_IDS", "TELEGRAM_USER_ID")

DISCORD_BOT_TOKEN = _secret("DISCORD_BOT_TOKEN")
DISCORD_ALLOWED_USERS = _id_set("DISCORD_ALLOWED_USER_IDS")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))  # 0 = any channel, but users must still be allowlisted
DISCORD_ENABLED = os.getenv("DISCORD_ENABLED", "false").lower() == "true"

SLACK_BOT_TOKEN = _secret("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = _secret("SLACK_APP_TOKEN")   # Socket Mode xapp-...
SLACK_ALLOWED_USERS = _id_set("SLACK_ALLOWED_USER_IDS")
SLACK_ENABLED = os.getenv("SLACK_ENABLED", "false").lower() == "true"

# The webhook listens on this computer only by default. Set WEBHOOK_HOST=0.0.0.0 ONLY
# if you truly need other machines to reach it, and put a proper secret and HTTPS in front.
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "127.0.0.1")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))
WEBHOOK_SECRET = _secret("WEBHOOK_SECRET")   # no default: the server refuses to start without a strong one
WEBHOOK_ENABLED = os.getenv("WEBHOOK_ENABLED", "true").lower() == "true"

# ── Event log (audit trail of every run) ───────────────────────────────────────
EVENTS_DB = os.getenv("EVENTS_DB", str(PROJECT_ROOT / "data" / "events.db"))

# ── Approvals ──────────────────────────────────────────────────────────────────
# Risky tools wait for the owner's yes. No answer in time means NO.
APPROVAL_TIMEOUT_SECONDS = int(os.getenv("APPROVAL_TIMEOUT_SECONDS", "300"))
# Runs with no live chat (cron, webhook, voice, Slack) send approval requests here.
APPROVAL_OWNER_CHANNEL = os.getenv("APPROVAL_OWNER_CHANNEL", "telegram").strip().lower()
# Tools that skip approval, comma separated. Empty by default. Think hard before adding
# run_command: it lets the agent do anything you can do on this computer without asking.
TRUSTED_TOOLS = {t.strip() for t in os.getenv("TRUSTED_TOOLS", "").split(",") if t.strip()}

# ── Paperclip (GawdBot as an employee in a Paperclip company) ─────────────────
# Off by default. When on, the webhook server accepts heartbeats at /paperclip/heartbeat.
PAPERCLIP_ENABLED = os.getenv("PAPERCLIP_ENABLED", "false").lower() == "true"
# The run token is only ever sent to these hosts. Paperclip runs locally by default.
PAPERCLIP_ALLOWED_HOSTS = {
    h.strip().lower() for h in os.getenv("PAPERCLIP_ALLOWED_HOSTS", "localhost,127.0.0.1,::1").split(",") if h.strip()
}
PAPERCLIP_MAX_ROUNDS = int(os.getenv("PAPERCLIP_MAX_ROUNDS", "20"))
# How long `python main.py heartbeat` waits for the running GawdBot to finish a task.
# Set Paperclip's timeoutSec for this agent a little higher than this.
PAPERCLIP_HEARTBEAT_TIMEOUT_SECONDS = int(os.getenv("PAPERCLIP_HEARTBEAT_TIMEOUT_SECONDS", "1800"))

# ── Web search ─────────────────────────────────────────────────────────────────
WEB_SEARCH_PROVIDER = os.getenv("WEB_SEARCH_PROVIDER", "duckduckgo")  # duckduckgo | brave
BRAVE_API_KEY = _secret("BRAVE_API_KEY")
WEB_SEARCH_MAX_RESULTS = int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5"))

# ── PC control ─────────────────────────────────────────────────────────────────
PC_CONTROL_ENABLED = os.getenv("PC_CONTROL_ENABLED", "true").lower() == "true"

# ── Self-evolution ─────────────────────────────────────────────────────────────
GITHUB_TOKEN = _secret("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")   # e.g. "user/gawdbote"
SELF_EVOLVE_BRANCH_PREFIX = os.getenv("SELF_EVOLVE_BRANCH_PREFIX", "evolve")
SELF_EVOLVE_AUTO_MERGE = os.getenv("SELF_EVOLVE_AUTO_MERGE", "false").lower() == "true"

# ── Cron scheduler ─────────────────────────────────────────────────────────────
CRON_ENABLED = os.getenv("CRON_ENABLED", "true").lower() == "true"
# JSON list of {"schedule": "0 9 * * *", "message": "Good morning! Summarize today's tasks."}
CRON_JOBS_JSON = os.getenv("CRON_JOBS", "[]")
