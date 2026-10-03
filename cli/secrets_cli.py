"""
Secrets command line tool.

  python main.py secrets status          show where each known secret comes from (never prints values)
  python main.py secrets set NAME        type a value in privately and save it to the OS vault
  python main.py secrets delete NAME     remove a secret from the vault
  python main.py secrets migrate         copy secret looking values from .env into the vault

After `migrate`, delete those lines from .env yourself. We never edit or delete your .env for you.
"""
from __future__ import annotations

import getpass
import os
import re
import sys

import config
from core import vault

KNOWN_SECRETS = [
    "NVIDIA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY",
    "TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN",
    "WEBHOOK_SECRET", "GITHUB_TOKEN", "BRAVE_API_KEY",
]

_SECRET_NAME_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)", re.I)
_PLACEHOLDER_RE = re.compile(r"(your|here|changeme|\.\.\.$|^$)", re.I)


def _need_vault() -> bool:
    if vault.available():
        return True
    print("No OS vault found. Install the keyring package: pip install keyring")
    print("On Linux you also need a Secret Service such as GNOME Keyring.")
    return False


def _status() -> None:
    print(f"Vault available: {'yes' if vault.available() else 'no'}\n")
    for name in KNOWN_SECRETS:
        if vault.get_secret(name):
            where = "vault"
        elif os.getenv(name):
            where = ".env / environment (move it to the vault)"
        else:
            where = "not set"
        print(f"  {name:<22} {where}")


def _set(name: str) -> None:
    if not _need_vault():
        return
    value = getpass.getpass(f"Value for {name} (hidden as you type): ").strip()
    if not value:
        print("Nothing entered. Nothing saved.")
        return
    print("Saved to the vault." if vault.set_secret(name, value) else "Could not save to the vault.")


def _delete(name: str) -> None:
    print("Deleted." if vault.delete_secret(name) else f"{name} was not in the vault.")


def _migrate() -> None:
    if not _need_vault():
        return
    from dotenv import dotenv_values
    env_path = config.PROJECT_ROOT / ".env"
    if not env_path.exists():
        print("No .env file found. Nothing to migrate.")
        return

    moved: list[str] = []
    for name, value in dotenv_values(env_path).items():
        if not value or not _SECRET_NAME_RE.search(name) or _PLACEHOLDER_RE.search(value):
            continue
        if vault.set_secret(name, value):
            moved.append(name)

    if not moved:
        print("No real secrets found in .env (placeholders are skipped).")
        return
    print("Copied to the vault:")
    for name in moved:
        print(f"  {name}")
    print("\nNEXT STEP: open .env and delete those lines. Until you do, the plain text copies still exist.")


def main(argv: list[str]) -> None:
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        _status()
    elif cmd == "set" and len(argv) == 2:
        _set(argv[1])
    elif cmd == "delete" and len(argv) == 2:
        _delete(argv[1])
    elif cmd == "migrate":
        _migrate()
    else:
        print(__doc__)
        sys.exit(1)
