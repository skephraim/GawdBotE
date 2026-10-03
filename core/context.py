"""
RunContext: the "encounter" for one agent run.

In an EHR every order, note and result hangs off an encounter, so you can pull up
everything that happened in one visit. A RunContext does the same for the agent:
every event and approval in a run carries its session_id.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

# An "asker" shows an approval request to the owner. It must not block until they answer;
# the answer comes back through core.approvals.resolve(). A CLI asker may answer right away.
Asker = Callable[["object"], Awaitable[None]]


@dataclass
class RunContext:
    source: str                                   # detailed label: telegram_voice, evolve/discord, cron
    channel: str = ""                             # who can approve: telegram, discord, cli
    ask: Optional[Asker] = None                   # how to reach the owner for approvals, if possible
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    approval_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def __post_init__(self) -> None:
        if not self.channel:
            self.channel = self.source.split("/")[-1].split("_")[0]
