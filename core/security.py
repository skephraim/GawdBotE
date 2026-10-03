"""
Security helpers shared by every tool and every interface.

Golden rule: FAIL CLOSED. When we are not sure something is safe, the answer is no.

What lives here:
  protected_reason()        decides if a file path holds secrets or internal data
  command_touches_secrets() a speed bump for obviously secret hunting shell commands
  scrubbed_env()            a copy of the environment with API keys and tokens removed
  is_authorized()           allowlist check for chat users (empty list means nobody)
  constant_time_equal()     compares secrets without leaking information through timing
  secret_is_weak()          rejects empty, default, or short secrets

IMPORTANT: command_touches_secrets() is NOT a wall. A determined command can dodge a
pattern list. The real fix is the approval gate and sandbox coming in Phase 1.
"""
from __future__ import annotations

import fnmatch
import hmac
import os
import re
from pathlib import Path
from typing import Iterable, Optional

import config

# ── File protection ────────────────────────────────────────────────────────────

# File names (matched case insensitively) that normally hold credentials.
SECRET_FILE_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.kdbx",
    "id_rsa*", "id_ed25519*", "id_ecdsa*", "credentials", "credentials.json",
    ".netrc", ".npmrc", ".pypirc",
)

# Template files are meant to be read. They hold placeholders, not real secrets.
SAFE_SUFFIXES = (".example", ".sample", ".template")

# Directories whose contents are never fair game for the agent's file tools.
SECRET_DIR_NAMES = {".ssh", ".aws", ".gnupg", ".azure", ".kube", ".git"}

# Project folders holding runtime data (memory database) and backups.
INTERNAL_DIRS = ("data", "backups")


def protected_reason(path: str | Path) -> Optional[str]:
    """
    Return a short reason if this path must not be read or written by the agent,
    or None if it is fine. We resolve the real path first, so a harmless looking
    symlink pointing at .env is still caught.
    """
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return "the path could not be resolved safely"

    parts = {p.lower() for p in resolved.parts}
    hit = parts & SECRET_DIR_NAMES
    if hit:
        return f"it is inside a protected folder ({sorted(hit)[0]})"

    root = config.PROJECT_ROOT.resolve()
    for name in INTERNAL_DIRS:
        guarded = root / name
        if resolved == guarded or guarded in resolved.parents:
            return f"it is inside the internal '{name}' folder"

    file_name = resolved.name.lower()
    if file_name.endswith(SAFE_SUFFIXES):
        return None
    for pattern in SECRET_FILE_PATTERNS:
        if fnmatch.fnmatch(file_name, pattern):
            return f"it looks like a secrets file ({resolved.name})"
    return None


# ── Shell speed bump ───────────────────────────────────────────────────────────

_SECRET_COMMAND_PATTERNS = [
    (re.compile(r"(^|[\s/\\'\"=<>|;&])\.env(?!\.(example|sample|template))\b", re.I), ".env file"),
    (re.compile(r"\.(ssh|aws|gnupg|kube|azure)\b", re.I), "a credentials folder"),
    (re.compile(r"\bid_(rsa|ed25519|ecdsa)\b", re.I), "an SSH key"),
    (re.compile(r"\.(netrc|npmrc|pypirc)\b", re.I), "a credentials file"),
    (re.compile(r"\bprintenv\b", re.I), "the environment dump command printenv"),
    (re.compile(r"(^|[;&|]\s*)env\s*($|[;&|>])", re.I), "the environment dump command env"),
    (re.compile(r"/proc/[^\s]*/environ", re.I), "process environment"),
    (re.compile(r"\bGet-ChildItem\s+Env:|\bdir\s+env:|\$env:\w*(KEY|TOKEN|SECRET|PASSWORD)", re.I), "environment variables"),
    (re.compile(r"(memory|events)\.db\b", re.I), "an internal database"),
]


def command_touches_secrets(command: str) -> Optional[str]:
    """Return a reason if the command obviously goes after secrets. This is a speed bump, not a wall."""
    for pattern, label in _SECRET_COMMAND_PATTERNS:
        if pattern.search(command):
            return f"it appears to touch {label}"
    return None


_SECRET_ENV_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.I)


def scrubbed_env() -> dict[str, str]:
    """
    Copy of os.environ with anything that looks like a secret removed.
    Pass this to every subprocess the agent starts, so `printenv` finds nothing good.
    """
    return {k: v for k, v in os.environ.items() if not _SECRET_ENV_RE.search(k)}


# ── Who is allowed to talk to the agent ────────────────────────────────────────

def is_authorized(user_id: object, allowed: Iterable[object]) -> bool:
    """
    Allowlist check. An EMPTY allowlist means nobody is allowed (fail closed).
    IDs are compared as text so 123 and "123" match.
    """
    allowed_set = {str(a).strip() for a in allowed if str(a).strip()}
    if not allowed_set:
        return False
    return str(user_id).strip() in allowed_set


# ── Secret comparison and quality ──────────────────────────────────────────────

def constant_time_equal(a: str, b: str) -> bool:
    """Compare two strings in constant time, so response timing cannot leak how many characters matched."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


_WEAK_SECRETS = {"", "changeme", "change-me", "secret", "password", "admin", "test", "default", "12345678"}


def secret_is_weak(secret: str) -> bool:
    """True for empty, well known default, or too short secrets (under 16 characters)."""
    s = (secret or "").strip()
    return s.lower() in _WEAK_SECRETS or len(s) < 16
