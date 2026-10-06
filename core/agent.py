"""
GawdBotE core agent: the agentic loop.

Receives a message from any interface (voice, Telegram, Discord, Slack, webhook, cron),
lets the LLM call tools until it has an answer, and returns that answer as text.

Every step is written to the event log (core/events.py), and every ASK tier tool goes
through the approval gate (core/approvals.py) before it runs.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from core import approvals, events, llm, memory, registry
from core import skills as skills_mod
from core.context import Asker, RunContext
import tools.builtin  # noqa: F401  (importing registers every built in tool)

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are GawdBotE, a powerful, self-improving AI assistant running on the owner's own computer.

Capabilities:
- Files & shell: read/write files, run commands, edit code
- Git & GitHub: commit, branch, push, create PRs
- PC control: mouse, keyboard, screen, open apps, clipboard
- Web search: search the internet for current information
- Memory: store and recall information persistently. Save durable things with memory_store and
  the right kind (fact, preference, decision, deadline with a due date, task, note), and list
  who or what it is about. Record clear relationships with memory_link. Use memory_about and
  memory_connect for "what do I know about X" and "how is X connected to Y". Do not save
  secrets, passwords, or other people's private details.
- Self-improvement: read and edit your own source code

Approvals:
- Some actions need the owner's approval. They are asked automatically; you do not ask in chat.
- If an action is not approved, do NOT retry it or work around it with another tool.
  Say what you wanted to do and why, and ask how the owner would like to proceed.

Guidelines:
- Be direct and concise in responses
- Prefer action over lengthy explanation
- When improving yourself: read CLAUDE.md first, change only what's needed, commit to a branch
- Ask one clarifying question if the request is genuinely ambiguous
- Use web search when you need current information you may not have"""


def tool_specs() -> list[dict]:
    """Every registered tool, in the format the LLM layer expects."""
    return registry.specs()


async def _dispatch(name: str, args: dict) -> str:
    """Run a tool with no approval check. Only _execute() should call this."""
    return await registry.call(name, args)


async def _execute(ctx: RunContext, call_id: str, name: str, raw_args: str) -> str:
    """Handle ONE tool call: parse, check risk, get approval, run, log. Always returns text."""
    try:
        args = json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ValueError("arguments must be a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        events.record(ctx, "tool_result", tool=name, status="error", preview=f"invalid arguments: {exc}")
        return f"Tool error: the arguments for {name} were not valid JSON ({exc}). Retry the call."

    spec = registry.get(name)
    risk = spec.risk_for(args) if spec else "unknown"
    events.record(ctx, "tool_call", tool=name, risk=risk, args=args, call_id=call_id)

    if spec is None:
        events.record(ctx, "tool_result", tool=name, status="error", preview="unknown tool")
        return f"Unknown tool: {name}. Use only the tools you were given."

    if risk != registry.SAFE:
        approved, reason = await approvals.request(ctx, name, args)
        if not approved:
            events.record(ctx, "tool_result", tool=name, status="blocked", preview=reason)
            return (f"NOT RUN: {name} was not approved because {reason}. "
                    "Do not retry it. Tell the owner what you wanted to do and ask how to proceed.")

    started = time.perf_counter()
    try:
        result = await _dispatch(name, args)
        status = "error" if result.startswith(("Tool error", "Refused", "Error")) else "ok"
    except Exception as exc:
        result, status = f"Tool error: {type(exc).__name__}: {exc}", "error"
    elapsed = int((time.perf_counter() - started) * 1000)
    events.record(ctx, "tool_result", tool=name, status=status, duration_ms=elapsed, preview=result[:500])
    log.debug("Tool %s -> %s (%dms)", name, result[:200], elapsed)
    return result


async def run(
    user_message: str,
    history: Optional[list[dict]] = None,
    source: str = "user",
    max_rounds: int = 10,
    *,
    ask: Optional[Asker] = None,
    channel: Optional[str] = None,
    ctx: Optional[RunContext] = None,
) -> str:
    """
    Run the agentic loop and return a final text response.

    history: prior {role, content} messages for multi turn context.
    source:  where the message came from (telegram, discord, voice, webhook, cron...)
    ask:     how to show approval requests to the owner on this channel, if possible
    channel: which channel may answer approvals (defaults from source)
    """
    ctx = ctx or RunContext(source=source, channel=channel or "", ask=ask)
    messages: list[dict] = list(history or [])
    messages.append({"role": "user", "content": user_message})

    skill_context = skills_mod.build_skill_context(user_message)

    # Hand the agent what it already knows: relevant saved memories and upcoming deadlines.
    # A memory failure must never stop the run, so any error just means "no recall this time".
    try:
        recalled = await memory.recall(user_message)
    except Exception as exc:
        log.warning("Memory recall failed: %s", exc)
        recalled = []

    effective_system = SYSTEM_PROMPT + (skill_context or "")
    # Saved memories travel separately so the LLM layer can withhold them, per brain, from any
    # provider that may train on prompts (brains marked shares_data, usually free tiers).
    private_context = memory.format_for_prompt(recalled)
    events.record(ctx, "run_start", message=user_message, source=source,
                  skills=[s.name for s in skills_mod.find_relevant(user_message)] if skill_context else [],
                  recalled=[r["id"] for r in recalled])

    specs = tool_specs()
    for round_num in range(1, max_rounds + 1):
        try:
            resp = await llm.chat(messages, tools=specs, system=effective_system, private_context=private_context)
        except Exception as exc:
            events.record(ctx, "run_end", status="error", rounds=round_num, final=f"LLM failure: {exc}")
            raise

        usage = resp.get("usage") or {}
        events.record(ctx, "llm_reply", provider=resp.get("provider"), round=round_num,
                      brain=resp.get("brain"), model=resp.get("model"), provider_name=resp.get("provider_name"),
                      served_model=resp.get("served_model"), memory_withheld=bool(resp.get("memory_withheld")),
                      input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0),
                      cached_input_tokens=usage.get("cached_input_tokens", 0), cost_usd=resp.get("cost_usd"),
                      fallbacks=resp.get("fallbacks") or [],
                      text=resp.get("content") or "", tools=[tc["name"] for tc in resp["tool_calls"]])

        if not resp["tool_calls"]:
            final = resp["content"]
            events.record(ctx, "run_end", status="ok", rounds=round_num, final=final)
            try:
                await memory.store(f"[{source}] User: {user_message}\nAssistant: {final}",
                                   source=source, kind="exchange")
            except Exception as exc:
                log.warning("Could not save this exchange to memory: %s", exc)
            return final

        messages.append({
            "role": "assistant",
            "content": resp["content"] or None,
            "tool_calls": [
                {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                for tc in resp["tool_calls"]
            ],
        })

        # Tool calls run in parallel. Approval prompts inside them still go one at a time.
        results = await asyncio.gather(
            *[_execute(ctx, tc["id"], tc["name"], tc["arguments"]) for tc in resp["tool_calls"]]
        )
        for tc, result in zip(resp["tool_calls"], results):
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

    final = "Max tool-call rounds reached. Please simplify the request."
    events.record(ctx, "run_end", status="max_rounds", rounds=max_rounds, final=final)
    return final
