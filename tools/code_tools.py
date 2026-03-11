"""
File I/O and shell execution tools for the coding agent.
"""
from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Optional

import config

ROOT = config.PROJECT_ROOT
_PROTECTED = {".env", ".git"}


def read_file(path: str) -> str:
    target = Path(path) if Path(path).is_absolute() else ROOT / path
    if not target.exists():
        return f"File not found: {path}"
    try:
        return target.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading {path}: {e}"


def write_file(path: str, content: str) -> str:
    target = Path(path) if Path(path).is_absolute() else ROOT / path
    if any(p in str(target) for p in _PROTECTED):
        return f"Refused: {path} is protected."
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Written: {target}"


async def run_command(command: str, cwd: Optional[str] = None) -> str:
    cwd = cwd or str(ROOT)
    try:
        proc = await asyncio.create_subprocess_shell(
            command, cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        out = stdout.decode().strip()
        err = stderr.decode().strip()
        parts = []
        if out:
            parts.append(out)
        if err:
            parts.append(f"stderr: {err}")
        return "\n".join(parts) or "(no output)"
    except asyncio.TimeoutError:
        return "Command timed out after 60 seconds."
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
