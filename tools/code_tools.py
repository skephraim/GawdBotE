"""
File I/O and shell execution tools for the coding agent.

SECURITY NOTES (Phase 0):
  * Both read_file and write_file refuse secrets files, credential folders, .git, and the
    internal data and backups folders. Paths are fully resolved first, so symlink tricks fail.
  * run_command starts the child process with a scrubbed environment (API keys and tokens
    removed) and refuses commands that obviously hunt for secrets.
  * That shell check is a speed bump, NOT a wall. Phase 1 adds the approval gate and sandbox.
"""
from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Optional

import config
from core import security

ROOT = config.PROJECT_ROOT

MAX_READ_BYTES = 1_000_000   # about 1 MB, keeps one huge file from flooding the model


def _resolve(path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else ROOT / p


def read_file(path: str) -> str:
    target = _resolve(path)
    if (reason := security.protected_reason(target)):
        return f"Refused: cannot read {path} because {reason}."
    if not target.exists():
        return f"File not found: {path}"
    if not target.is_file():
        return f"Not a file: {path}"
    try:
        if target.stat().st_size > MAX_READ_BYTES:
            return f"Refused: {path} is larger than {MAX_READ_BYTES:,} bytes. Read it in parts with a command instead."
        return target.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading {path}: {e}"


def write_file(path: str, content: str) -> str:
    target = _resolve(path)
    if (reason := security.protected_reason(target)):
        return f"Refused: cannot write {path} because {reason}."
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Written: {target}"


async def run_command(command: str, cwd: Optional[str] = None) -> str:
    if (reason := security.command_touches_secrets(command)):
        return f"Refused: this command was blocked because {reason}."
    cwd = cwd or str(ROOT)
    try:
        proc = await asyncio.create_subprocess_shell(
            command, cwd=cwd,
            env=security.scrubbed_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()   # do not leave a runaway process behind
            await proc.wait()
            return "Command timed out after 60 seconds and was stopped."
        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        parts = []
        if out:
            parts.append(out)
        if err:
            parts.append(f"stderr: {err}")
        return "\n".join(parts) or "(no output)"
    except Exception as e:
        return f"Command error: {e}"


def list_project_files() -> str:
    files = []
    for ext in ["*.py", "*.md", "*.json", "*.yaml", "*.yml", "*.toml", "*.txt"]:
        for f in ROOT.rglob(ext):
            rel = f.relative_to(ROOT)
            parts = rel.parts
            if any(p.startswith(".") or p in ("__pycache__", "data", "node_modules") for p in parts):
                continue
            files.append(str(rel))
    return "\n".join(sorted(files)) or "No files found."
