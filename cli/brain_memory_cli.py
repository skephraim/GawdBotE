"""
Brain, memory and usage commands.

  python main.py brain                    show every chain and which brains are ready
  python main.py brain use <name>         put a brain first in the main chain
  python main.py brain reset              go back to the configured order
  python main.py brain test               send a tiny prompt down the main chain (costs a few tokens)

  python main.py memory                   counts by kind, which models made the vectors
  python main.py memory search <words>    hybrid keyword plus meaning search
  python main.py memory upcoming [days]   deadlines and tasks due soon
  python main.py memory forget <id>       delete one memory
  python main.py memory prune [days]      delete chat exchanges older than N days
  python main.py memory reembed           re-vector durable memories with the current embedding brain

  python main.py usage [days]             tokens and cost per brain over the last N days (default 30)
"""
from __future__ import annotations

import asyncio
import json
import sys

from core import brains, events, llm, memory


def brain_main(argv: list[str]) -> None:
    if argv[:1] == ["test"]:
        async def ping():
            resp = await llm.chat([{"role": "user", "content": "Reply with the single word OK."}])
            for problem in resp.get("fallbacks") or []:
                print(f"  fallback: {problem}")
            u = resp.get("usage") or {}
            print(f"Answered by {resp['brain']} ({resp['model']}): {resp['content']!r} "
                  f"[{u.get('input_tokens', 0)} in, {u.get('output_tokens', 0)} out]")
        try:
            asyncio.run(ping())
        except RuntimeError as exc:
            print(exc)
            sys.exit(1)
        return
    print(brains.handle_command(argv))


def memory_main(argv: list[str]) -> None:
    cmd = argv[0] if argv else "stats"
    if cmd == "stats":
        print(json.dumps(memory.stats(), indent=2))
    elif cmd == "search" and len(argv) > 1:
        print(memory.format_results(asyncio.run(memory.search(" ".join(argv[1:])))))
    elif cmd == "upcoming":
        print(memory.format_results(memory.upcoming(int(argv[1]) if len(argv) > 1 else 7)))
    elif cmd == "forget" and len(argv) == 2 and argv[1].isdigit():
        print("Deleted." if memory.forget(int(argv[1])) else "No such memory.")
    elif cmd == "prune":
        days = int(argv[1]) if len(argv) > 1 else None
        print(f"Deleted {memory.prune_exchanges(days):,} old chat exchanges.")
    elif cmd == "reembed":
        count, model = asyncio.run(memory.reembed())
        print(f"Re-embedded {count:,} memories with {model}." if model else "No embedding brain is available.")
    else:
        print(__doc__)
        sys.exit(1)


def usage_main(argv: list[str]) -> None:
    days = int(argv[0]) if argv and argv[0].isdigit() else 30
    print(events.format_usage(events.usage_summary(days), f"Brain usage, last {days} days:"))
