"""
Approval gate: risky tool calls wait for the owner's yes.

Flow, like a pharmacist verifying a high alert order before it is dispensed:

  1. The agent wants to run an ASK tier tool.
  2. request() creates a pending approval with a short random id, e.g. 3f9a1c.
  3. The owner is shown the exact tool and arguments on the channel they are using
     (CLI prompt, Telegram message, Discord message). If the run has no live channel
     (cron, webhook, voice, Slack), the request goes to the OWNER CHANNEL instead,
     normally Telegram, so unattended jobs can still reach you on your phone.
  4. The owner answers (/approve 3f9a1c or /deny 3f9a1c). resolve() wakes the agent.
  5. No answer before the timeout means DENIED. Nobody able to answer means DENIED.

Requests within one run are shown ONE AT A TIME, so you never face a pile of prompts.
Only the channel that was asked can answer, so a Discord user cannot approve a Telegram prompt.
Tools listed in TRUSTED_TOOLS skip the prompt. Use that sparingly, and never for run_command
unless you accept that the agent can do anything you can do on this computer.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import config
from core import events
from core.context import RunContext

log = logging.getLogger(__name__)

PREVIEW_CHARS = 3_000


@dataclass
class ApprovalRequest:
    id: str
    tool: str
    args: dict
    source: str
    session_id: str
    channel: str                       # the only channel allowed to answer
    timeout: float
    future: asyncio.Future = field(repr=False)

    def args_preview(self) -> str:
        text = json.dumps(events.redact(self.args, limit=PREVIEW_CHARS), indent=1, ensure_ascii=False)
        if len(text) > PREVIEW_CHARS:
            return text[:PREVIEW_CHARS] + "\n[... TRUNCATED. You cannot see all of it, so deny unless you are sure.]"
        return text

    def prompt_text(self, approve_cmd: str = "/approve", deny_cmd: str = "/deny") -> str:
        minutes = max(1, round(self.timeout / 60))
        return (
            f"APPROVAL NEEDED [{self.id}]\n"
            f"From: {self.source}\n"
            f"Tool: {self.tool}\n"
            f"Arguments:\n{self.args_preview()}\n\n"
            f"Reply {approve_cmd} {self.id} or {deny_cmd} {self.id}. "
            f"No answer in {minutes} min means no."
        )


_pending: dict[str, ApprovalRequest] = {}
_owner_channel: Optional[tuple[str, Callable[[ApprovalRequest], Awaitable[None]]]] = None


def register_owner_channel(channel: str, asker: Callable[[ApprovalRequest], Awaitable[None]]) -> None:
    """Called by an interface at startup (Telegram by default) to receive approvals for runs with no live channel."""
    global _owner_channel
    if channel != config.APPROVAL_OWNER_CHANNEL:
        return
    _owner_channel = (channel, asker)
    log.info("Approvals for unattended runs will be sent to %s", channel)


def clear_owner_channel() -> None:
    global _owner_channel
    _owner_channel = None


def pending() -> list[ApprovalRequest]:
    return list(_pending.values())


def _new_id() -> str:
    while True:
        rid = secrets.token_hex(3)          # 6 hex characters, unpredictable
        if rid not in _pending:
            return rid


async def request(ctx: RunContext, tool: str, args: dict, timeout: Optional[float] = None) -> tuple[bool, str]:
    """
    Ask the owner whether `tool` may run with `args`.
    Returns (approved, reason). Never raises: any failure means not approved.
    """
    if tool in config.TRUSTED_TOOLS:
        events.record(ctx, "approval", tool=tool, status="trusted", by="TRUSTED_TOOLS setting")
        return True, "trusted in settings"

    if ctx.ask is not None:
        channel, asker = ctx.channel, ctx.ask
    elif _owner_channel is not None:
        channel, asker = _owner_channel
    else:
        events.record(ctx, "approval", tool=tool, status="unavailable")
        return False, f"it needs the owner's approval, and nobody can approve from the '{ctx.source}' channel"

    timeout = float(timeout if timeout is not None else config.APPROVAL_TIMEOUT_SECONDS)
    req = ApprovalRequest(
        id=_new_id(), tool=tool, args=args, source=ctx.source, session_id=ctx.session_id,
        channel=channel, timeout=timeout, future=asyncio.get_running_loop().create_future(),
    )

    async with ctx.approval_lock:            # one prompt at a time per run
        _pending[req.id] = req
        events.record(ctx, "approval", tool=tool, status="requested", request_id=req.id, asked_on=channel)
        try:
            await asker(req)
            approved, by = await asyncio.wait_for(req.future, timeout)
        except asyncio.TimeoutError:
            events.record(ctx, "approval", tool=tool, status="timeout", request_id=req.id)
            return False, f"the owner did not answer within {round(timeout)} seconds"
        except Exception as exc:
            log.warning("Approval prompt failed for %s: %s", tool, exc)
            events.record(ctx, "approval", tool=tool, status="unavailable", error=str(exc))
            return False, "the approval request could not be delivered"
        finally:
            _pending.pop(req.id, None)

    events.record(ctx, "approval", tool=tool, status="approved" if approved else "denied", by=by, request_id=req.id)
    return (True, f"approved by {by}") if approved else (False, f"the owner ({by}) denied it")


def resolve(request_id: str, approved: bool, by: str, channel: str) -> str:
    """Answer a pending request. Returns a short message to show the person who answered."""
    rid = (request_id or "").strip().lower().lstrip("#[").rstrip("]")
    req = _pending.get(rid)
    if req is None:
        return f"No pending approval with id {rid or '(blank)'}. It may have expired or already been answered."
    if req.channel != channel:
        return f"Approval {rid} must be answered on {req.channel}, not {channel}."
    if req.future.done():
        return f"Approval {rid} was already answered."
    req.future.set_result((approved, by))
    return f"{'Approved' if approved else 'Denied'}: {req.tool} [{rid}]."
