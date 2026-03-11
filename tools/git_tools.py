"""
Git operations for the coding agent and self-evolution.
"""
from __future__ import annotations
import subprocess
from datetime import datetime
from typing import Optional

import config

ROOT = str(config.PROJECT_ROOT)


def _run(cmd: list[str], cwd: str = ROOT) -> str:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    out = result.stdout.strip()
    err = result.stderr.strip()
    if result.returncode != 0:
        return f"Error: {err or out}"
    return out or "OK"


def status() -> str:
    return _run(["git", "status", "--short"])


def diff(file: Optional[str] = None) -> str:
    cmd = ["git", "diff"]
    if file:
        cmd.append(file)
    return _run(cmd)


def log(n: int = 10) -> str:
    return _run(["git", "log", f"-{n}", "--oneline"])


def commit(message: str, files: list[str]) -> str:
    for f in files:
        _run(["git", "add", f])
    return _run(["git", "commit", "-m", message])


def create_branch(name: str) -> str:
    return _run(["git", "checkout", "-b", name])


def current_branch() -> str:
    return _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])


def push(branch: Optional[str] = None) -> str:
    branch = branch or current_branch()
    return _run(["git", "push", "-u", "origin", branch])


def merge_to_main(branch: str) -> str:
    _run(["git", "checkout", "main"])
    result = _run(["git", "merge", "--no-ff", branch, "-m", f"Auto-merge: {branch}"])
    _run(["git", "push", "origin", "main"])
    return result


def generate_evolve_branch() -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{config.SELF_EVOLVE_BRANCH_PREFIX}-{ts}"


async def create_pr(title: str, body: str, head: str, base: str = "main") -> str:
    if not config.GITHUB_TOKEN or not config.GITHUB_REPO:
        return "GitHub not configured. Set GITHUB_TOKEN and GITHUB_REPO in .env"
    import aiohttp
    url = f"https://api.github.com/repos/{config.GITHUB_REPO}/pulls"
    headers = {
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json={"title": title, "body": body, "head": head, "base": base}, headers=headers) as resp:
            data = await resp.json()
            if resp.status == 201:
                return f"PR created: {data['html_url']}"
            return f"PR failed ({resp.status}): {data.get('message', 'unknown error')}"
