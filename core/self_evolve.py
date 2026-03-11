"""
Self-evolution: SuperAI edits its own source code.
Workflow:
  1. Read CLAUDE.md for architecture context
  2. Agent edits source files based on the improvement request
  3. Run syntax checks (python -m py_compile)
  4. Commit to a new branch
  5. Create GitHub PR (or auto-merge if configured)
"""
from __future__ import annotations
import logging

import config
from core import agent, memory
from tools import git_tools, code_tools

log = logging.getLogger(__name__)

EVOLVE_SYSTEM = """You are the self-evolution subsystem of SuperAI.
Your job is to improve the SuperAI codebase based on the user's request.

Steps you MUST follow:
1. Read CLAUDE.md (call read_file("CLAUDE.md")) to understand the architecture.
2. Read any relevant source files before modifying them.
3. Make the smallest correct change that fulfills the request.
4. Verify syntax: run `python -m py_compile <file>` for each file you change.
5. Use git_create_branch to create a branch named evolve-YYYYMMDD-HHMMSS.
6. Commit all changed files with a clear message.
7. Push the branch.
8. Create a GitHub PR with the changes described.

Do not modify .env, .git, or data/ directories.
Do not break existing functionality."""


async def evolve(request: str, source: str = "user") -> str:
    """
    Trigger a self-improvement cycle.
    Returns a summary of what was done.
    """
    log.info("Self-evolution requested: %r from %s", request, source)

    # Inject evolve-specific system context into the agent run
    # We override the system prompt via a meta-instruction in the user message
    evolve_prompt = (
        f"SELF-EVOLUTION REQUEST:\n{request}\n\n"
        f"System instructions for this task:\n{EVOLVE_SYSTEM}\n\n"
        "Begin by reading CLAUDE.md, then proceed with the changes."
    )

    result = await agent.run(evolve_prompt, source=f"evolve/{source}", max_rounds=20)

    # Store what we evolved
    await memory.store(
        f"Self-evolution completed: {request}\nResult: {result[:300]}",
        source="self_evolve",
    )

    return result
