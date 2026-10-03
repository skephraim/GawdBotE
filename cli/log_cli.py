"""
Event log viewer.

  python main.py log                     the 20 most recent runs
  python main.py log <session_id>        full step by step trace of one run
  python main.py log --channel telegram  recent runs from one channel only
  python main.py log prune --days 90     delete events older than 90 days
"""
from __future__ import annotations

import sys

from core import events


def main(argv: list[str]) -> None:
    if argv[:1] == ["prune"]:
        if len(argv) != 3 or argv[1] != "--days" or not argv[2].isdigit():
            print(__doc__)
            sys.exit(1)
        print(f"Deleted {events.prune(int(argv[2])):,} events older than {argv[2]} days.")
        return

    if argv[:1] == ["--channel"] and len(argv) == 2:
        print(events.format_recent(20, channel=argv[1]))
        return

    if len(argv) == 1 and not argv[0].startswith("-"):
        print(events.format_session(argv[0], max_chars=100_000))
        return

    if argv:
        print(__doc__)
        sys.exit(1)

    print(events.format_recent(20))
