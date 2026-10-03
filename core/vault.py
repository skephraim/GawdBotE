"""
Secret vault: store API keys in the operating system's own credential store
instead of a plain text .env file.

  Windows  -> Windows Credential Locker
  macOS    -> Keychain
  Linux    -> Secret Service (GNOME Keyring, KWallet) when available

Why: a .env file is a sticky note on your monitor. The vault is the dispensing cabinet.
Anything running as you can read the sticky note, but the cabinet hands out one item
at a time, and our tools never pass the real value to the AI model.

This module never imports config (config imports us), so there is no circular import.
Every function fails quietly when keyring is missing so the bot still runs with .env.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

SERVICE = "gawdbot2"


def _keyring():
    try:
        import keyring
        return keyring
    except ImportError:
        return None


def available() -> bool:
    """True if the keyring package is installed and has a working backend."""
    kr = _keyring()
    if kr is None:
        return False
    try:
        backend = kr.get_keyring()
        # The fail backend means "no real vault on this machine".
        return "fail" not in type(backend).__module__.lower()
    except Exception:
        return False


def get_secret(name: str) -> Optional[str]:
    """Look up a secret by name. Returns None if missing or the vault is unavailable."""
    kr = _keyring()
    if kr is None:
        return None
    try:
        return kr.get_password(SERVICE, name)
    except Exception as exc:
        log.debug("Vault read failed for %s: %s", name, exc)
        return None


def set_secret(name: str, value: str) -> bool:
    """Store a secret. Returns True on success."""
    kr = _keyring()
    if kr is None:
        return False
    try:
        kr.set_password(SERVICE, name, value)
        return True
    except Exception as exc:
        log.warning("Vault write failed for %s: %s", name, exc)
        return False


def delete_secret(name: str) -> bool:
    """Remove a secret. Returns True if something was deleted."""
    kr = _keyring()
    if kr is None:
        return False
    try:
        kr.delete_password(SERVICE, name)
        return True
    except Exception:
        return False
