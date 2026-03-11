"""
SuperAI doctor — health checks for all subsystems.
Inspired by OpenClaw's `openclaw doctor` command.
Usage: python -m cli.doctor [--fix]
"""
from __future__ import annotations
import asyncio
import importlib
import shutil
import sys
from pathlib import Path


CHECKS = []

def check(name: str):
    def decorator(fn):
        CHECKS.append((name, fn))
        return fn
    return decorator


@check("Python version")
def check_python():
    v = sys.version_info
    ok = v >= (3, 11)
    return ok, f"Python {v.major}.{v.minor}.{v.micro}" + ("" if ok else " (need 3.11+)")


@check("Config / .env")
def check_env():
    env = Path(".env")
    if not env.exists():
        return False, ".env not found — copy .env.example and fill in values"
    return True, ".env present"


@check("Data directory")
def check_data_dir():
    d = Path("data")
    d.mkdir(exist_ok=True)
    return True, f"data/ exists at {d.resolve()}"


@check("aiohttp")
def check_aiohttp():
    return _pkg("aiohttp")


@check("openai")
def check_openai():
    return _pkg("openai")


@check("python-dotenv")
def check_dotenv():
    return _pkg("dotenv", pip_name="python-dotenv")


@check("LLM connectivity")
def check_llm():
    import config
    providers = config.LLM_PROVIDERS
    if "nvidia" in providers and not config.NVIDIA_API_KEY:
        return False, "NVIDIA_API_KEY not set"
    if "openai" in providers and not config.OPENAI_API_KEY:
        return False, "OPENAI_API_KEY not set"
    if "anthropic" in providers and not config.ANTHROPIC_API_KEY:
        return False, "ANTHROPIC_API_KEY not set"
    if "openrouter" in providers and not config.OPENROUTER_API_KEY:
        return False, "OPENROUTER_API_KEY not set"
    return True, f"Providers configured: {', '.join(providers)}"


@check("Telegram")
def check_telegram():
    import config
    if not config.TELEGRAM_BOT_TOKEN:
        return False, "TELEGRAM_BOT_TOKEN not set"
    return _pkg("telegram", pip_name="python-telegram-bot")


@check("Discord")
def check_discord():
    import config
    if not config.DISCORD_ENABLED:
        return None, "Disabled (DISCORD_ENABLED=false)"
    if not config.DISCORD_BOT_TOKEN:
        return False, "DISCORD_BOT_TOKEN not set"
    return _pkg("discord", pip_name="discord.py")


@check("Slack")
def check_slack():
    import config
    if not config.SLACK_ENABLED:
        return None, "Disabled (SLACK_ENABLED=false)"
    if not config.SLACK_BOT_TOKEN:
        return False, "SLACK_BOT_TOKEN not set"
    return _pkg("slack_bolt", pip_name="slack-bolt")


@check("PC control (pyautogui)")
def check_pc_control():
    import config
    if not config.PC_CONTROL_ENABLED:
        return None, "Disabled (PC_CONTROL_ENABLED=false)"
    ok, msg = _pkg("pyautogui")
    if not ok:
        return ok, msg
    for tool in ["wmctrl", "xclip"]:
        if not shutil.which(tool):
            return False, f"{tool} not found — run: sudo apt install {tool}"
    return True, "pyautogui + wmctrl + xclip"


@check("Voice (faster-whisper)")
def check_voice():
    import config
    if not config.VOICE_ENABLED:
        return None, "Disabled (VOICE_ENABLED=false)"
    ok, msg = _pkg("faster_whisper", pip_name="faster-whisper")
    if not ok:
        return ok, msg
    if not shutil.which("piper"):
        return False, "piper not found — install from https://github.com/rhasspy/piper"
    if not shutil.which("aplay"):
        return False, "aplay not found — run: sudo apt install alsa-utils"
    return True, "faster-whisper + piper + aplay"


@check("Web search")
def check_web_search():
    import config
    if config.WEB_SEARCH_PROVIDER == "brave" and not config.BRAVE_API_KEY:
        return False, "WEB_SEARCH_PROVIDER=brave but BRAVE_API_KEY not set"
    return True, f"Provider: {config.WEB_SEARCH_PROVIDER}"


@check("Git")
def check_git():
    if not shutil.which("git"):
        return False, "git not found"
    return True, "git available"


@check("Cron (croniter)")
def check_cron():
    import config
    if not config.CRON_ENABLED:
        return None, "Disabled (CRON_ENABLED=false)"
    return _pkg("croniter")


def _pkg(module: str, pip_name: str = None) -> tuple:
    try:
        importlib.import_module(module)
        return True, f"{pip_name or module} installed"
    except ImportError:
        return False, f"{pip_name or module} not installed — run: pip install {pip_name or module}"


def main(fix: bool = False) -> None:
    print("\nSuperAI Doctor\n" + "=" * 40)
    all_ok = True
    for name, fn in CHECKS:
        try:
            result = fn()
        except Exception as e:
            result = (False, str(e))

        status, msg = result
        if status is True:
            symbol = "✓"
        elif status is None:
            symbol = "~"  # skipped/disabled
        else:
            symbol = "✗"
            all_ok = False

        print(f"  {symbol}  {name}: {msg}")

    print()
    if all_ok:
        print("All checks passed!")
    else:
        print("Some checks failed. Review above output and fix issues.")
        if not fix:
            print("Tip: run with --fix to attempt automatic fixes (coming soon)")
    print()


if __name__ == "__main__":
    import sys
    main(fix="--fix" in sys.argv)
