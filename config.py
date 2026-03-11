"""
SuperAI — unified configuration.
All settings come from environment variables / .env file.
"""
from __future__ import annotations
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Project root ───────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent

# ── LLM providers (tried in order for fallback) ────────────────────────────────
# Comma-separated list: nvidia,openrouter,ollama,openai,anthropic
# OpenRouter is the default cloud backup — it can route to almost any model
LLM_PROVIDERS = [p.strip() for p in os.getenv("LLM_PROVIDERS", "nvidia,openrouter,ollama").split(",")]

# NVIDIA NIM
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
NVIDIA_MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
NVIDIA_VISION_MODEL = os.getenv("NVIDIA_VISION_MODEL", "meta/llama-3.2-11b-vision-instruct")

# Ollama (local)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.3")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "llava")

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# Anthropic
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-6")

# OpenRouter
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

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

# ── Interfaces ─────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_USER_ID = int(os.getenv("TELEGRAM_USER_ID", "0"))

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))  # 0 = respond in any
DISCORD_ENABLED = os.getenv("DISCORD_ENABLED", "false").lower() == "true"

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN", "")   # Socket Mode xapp-...
SLACK_ENABLED = os.getenv("SLACK_ENABLED", "false").lower() == "true"

WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "changeme")
WEBHOOK_ENABLED = os.getenv("WEBHOOK_ENABLED", "true").lower() == "true"

# ── Web search ─────────────────────────────────────────────────────────────────
WEB_SEARCH_PROVIDER = os.getenv("WEB_SEARCH_PROVIDER", "duckduckgo")  # duckduckgo | brave
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
WEB_SEARCH_MAX_RESULTS = int(os.getenv("WEB_SEARCH_MAX_RESULTS", "5"))

# ── PC control ─────────────────────────────────────────────────────────────────
PC_CONTROL_ENABLED = os.getenv("PC_CONTROL_ENABLED", "true").lower() == "true"

# ── Self-evolution ─────────────────────────────────────────────────────────────
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")   # e.g. "user/superai"
SELF_EVOLVE_BRANCH_PREFIX = os.getenv("SELF_EVOLVE_BRANCH_PREFIX", "evolve")
SELF_EVOLVE_AUTO_MERGE = os.getenv("SELF_EVOLVE_AUTO_MERGE", "false").lower() == "true"

# ── Cron scheduler ─────────────────────────────────────────────────────────────
CRON_ENABLED = os.getenv("CRON_ENABLED", "true").lower() == "true"
# JSON list of {"schedule": "0 9 * * *", "message": "Good morning! Summarize today's tasks."}
CRON_JOBS_JSON = os.getenv("CRON_JOBS", "[]")
