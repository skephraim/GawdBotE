"""
`python main.py heartbeat`: the command Paperclip runs to wake GawdBot.

Set it up in Paperclip as a "process" adapter agent with:
    command:     python main.py heartbeat
    cwd:         the GawdBotE folder
    timeoutSec:  a little more than PAPERCLIP_HEARTBEAT_TIMEOUT_SECONDS (default 1800)

Paperclip starts this command with PAPERCLIP_API_URL, PAPERCLIP_API_KEY, PAPERCLIP_RUN_ID
and friends in the environment. What happens next:

  1. We forward those values to the GawdBot that is ALREADY RUNNING (python main.py), over
     localhost, using the webhook secret. That process does the work, so approvals for
     risky actions can reach you on Telegram. This command waits and prints the result,
     which Paperclip shows in its run view.
  2. If GawdBot is not running, we do the work right here instead. Nothing can reach you
     for approval in this mode, so every risky action is refused (fail closed). Start
     GawdBot with `python main.py` for full capability.

Exit codes: 0 success or nothing to do, 1 the heartbeat failed, 2 setup problem.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

import config
from core import paperclip, security

log = logging.getLogger(__name__)


class DaemonUnavailable(Exception):
    pass


async def _forward(env: paperclip.HeartbeatEnv) -> dict:
    """Send the heartbeat to the running GawdBot and wait for its answer."""
    import aiohttp
    url = f"http://127.0.0.1:{config.WEBHOOK_PORT}/paperclip/heartbeat"
    timeout = aiohttp.ClientTimeout(total=config.PAPERCLIP_HEARTBEAT_TIMEOUT_SECONDS)
    headers = {"X-Webhook-Secret": config.WEBHOOK_SECRET}
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(url, json={"env": env.to_mapping()}, headers=headers) as resp:
                if resp.status in (401, 404):
                    raise DaemonUnavailable(
                        "GawdBot answered but refused the heartbeat "
                        f"(HTTP {resp.status}: check PAPERCLIP_ENABLED=true and WEBHOOK_SECRET)"
                    )
                return await resp.json(content_type=None)
    except aiohttp.ClientConnectorError as exc:
        raise DaemonUnavailable(f"GawdBot is not running on port {config.WEBHOOK_PORT}") from exc


def _print(result: dict) -> None:
    # Paperclip captures stdout for its run view. Never print the token.
    print(f"[gawdbot] outcome: {result.get('outcome')}")
    print(f"[gawdbot] {result.get('message')}")
    if result.get("issue_id"):
        print(f"[gawdbot] task: {result['issue_id']}")
    if result.get("session_id"):
        print(f"[gawdbot] trace: python main.py log {result['session_id']}")


async def run(environ: dict[str, str]) -> int:
    env = paperclip.HeartbeatEnv.from_mapping(environ)
    if (problem := env.problem()):
        print(f"[gawdbot] cannot start heartbeat: {problem}")
        return 2

    usable_daemon = config.WEBHOOK_ENABLED and not security.secret_is_weak(config.WEBHOOK_SECRET)
    if usable_daemon:
        try:
            print("[gawdbot] handing the heartbeat to the running GawdBot")
            result = await _forward(env)
            _print(result)
            return 0 if result.get("ok") else 1
        except DaemonUnavailable as exc:
            print(f"[gawdbot] {exc}")

    print("[gawdbot] running standalone. No approval channel here, so risky actions will be refused.")
    result = await paperclip.run_heartbeat(env)
    _print(result.as_dict())
    return 0 if result.ok else 1


def main() -> None:
    sys.exit(asyncio.run(run(dict(os.environ))))
