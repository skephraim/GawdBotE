"""
Built in tools, registered with the @tool decorator.

Each tool is defined exactly once here. The decorator turns the type hints and the
docstring into the schema the AI model sees, and the risk decides whether the owner
must approve the call (see core/registry.py).

Risk guide used in this file:
  SAFE  reads local info or searches: no change to the machine, nothing sent as you
  ASK   writes files, runs commands, touches git remotes, drives the mouse and keyboard,
        reads the screen or clipboard (often private), installs code
"""
from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Literal, Optional

import config
from core import memory, security
from core import skills as skills_mod
from core.registry import ASK, SAFE, tool
from tools import code_tools, git_tools, pc_control, web_search


# ── Files and shell ────────────────────────────────────────────────────────────

def _read_risk(args: dict) -> str:
    """Reading inside the project is SAFE. Reading anywhere else on the disk needs approval."""
    path = Path(str(args.get("path", ""))).expanduser()
    target = (path if path.is_absolute() else config.PROJECT_ROOT / path).resolve()
    root = config.PROJECT_ROOT.resolve()
    return SAFE if (target == root or root in target.parents) else ASK


@tool(risk=_read_risk)
def read_file(path: str) -> str:
    """Read a text file. Secrets files are always refused.

    Args:
        path: File path, relative to the project folder or absolute.
    """
    return code_tools.read_file(path)


@tool(risk=ASK)
def write_file(path: str, content: str) -> str:
    """Create or overwrite a text file.

    Args:
        path: File path, relative to the project folder or absolute.
        content: The full new contents of the file.
    """
    return code_tools.write_file(path, content)


@tool(risk=ASK)
async def run_command(command: str, cwd: Optional[str] = None) -> str:
    """Run a shell command and return its output. Times out after 60 seconds.

    Args:
        command: The command line to run.
        cwd: Working folder. Defaults to the project folder.
    """
    return await code_tools.run_command(command, cwd)


@tool(risk=SAFE)
def list_project_files() -> str:
    """List this project's source files."""
    return code_tools.list_project_files()


# ── Memory ─────────────────────────────────────────────────────────────────────

MemoryKind = Literal["fact", "preference", "decision", "deadline", "task", "note"]


def _parse_due(due: Optional[str]) -> Optional[float]:
    """'2026-10-15' or '2026-10-15 17:00' in local time, to a timestamp. Raises ValueError if unreadable."""
    if not due:
        return None
    from datetime import datetime
    return datetime.fromisoformat(due.strip().replace("T", " ")).timestamp()


@tool(risk=SAFE)
async def memory_store(content: str, kind: MemoryKind = "fact", due: Optional[str] = None) -> str:
    """Save something to long term memory so you remember it in future conversations.

    Args:
        content: One clear standalone statement, e.g. "Sun prefers plain prose with no dashes".
        kind: fact, preference, decision, deadline, task, or note.
        due: For deadlines and tasks, the due date as YYYY-MM-DD or YYYY-MM-DD HH:MM (local time).
    """
    try:
        due_ts = _parse_due(due)
    except ValueError:
        return f"Tool error: could not read the due date {due!r}. Use YYYY-MM-DD or YYYY-MM-DD HH:MM."
    mid = await memory.store(content, kind=kind, due_ts=due_ts)
    return f"Saved as memory #{mid} ({kind})."


@tool(risk=SAFE)
async def memory_search(query: str, kind: Optional[MemoryKind] = None) -> str:
    """Search long term memory by keywords and meaning.

    Args:
        query: What to look for.
        kind: Only search one kind of memory. Omit to search everything.
    """
    return memory.format_results(await memory.search(query, kinds=[kind] if kind else None))


@tool(risk=SAFE)
def memory_upcoming(days: int = 7) -> str:
    """List remembered deadlines and tasks due soon, plus anything overdue.

    Args:
        days: How many days ahead to look.
    """
    return memory.format_results(memory.upcoming(days))


@tool(risk=ASK)
def memory_forget(memory_id: int) -> str:
    """Permanently delete one memory by its number. Needs approval.

    Args:
        memory_id: The memory number shown as #id in search results.
    """
    return f"Memory #{memory_id} deleted." if memory.forget(memory_id) else f"No memory #{memory_id}."


# ── Web ────────────────────────────────────────────────────────────────────────

# The Python function can't be called web_search (that is the module we import), so we
# give the tool its public name explicitly. The model has always known it as web_search.
@tool(risk=SAFE, name="web_search")
async def web_search_tool(query: str, max_results: int = 5) -> str:
    """Search the web for current information.

    Args:
        query: Search query.
        max_results: Number of results.
    """
    return await web_search.search(query, max_results=max_results)


# ── Git and GitHub ─────────────────────────────────────────────────────────────

@tool(risk=SAFE)
def git_status() -> str:
    """Show which files changed in the project's git repository."""
    return git_tools.status()


@tool(risk=SAFE)
def git_diff(file: Optional[str] = None) -> str:
    """Show the exact line changes not yet committed.

    Args:
        file: Limit the diff to one file. Omit for all files.
    """
    return git_tools.diff(file)


@tool(risk=SAFE)
def git_log(n: int = 10) -> str:
    """Show recent commits.

    Args:
        n: How many commits to show.
    """
    return git_tools.log(n)


@tool(risk=ASK)
def git_commit(message: str, files: list[str]) -> str:
    """Commit files to git.

    Args:
        message: The commit message.
        files: Paths of the files to include.
    """
    return git_tools.commit(message, files)


@tool(risk=ASK)
def git_create_branch(name: str) -> str:
    """Create a new git branch and switch to it.

    Args:
        name: The new branch name.
    """
    return git_tools.create_branch(name)


@tool(risk=ASK)
def git_push() -> str:
    """Push the current branch to GitHub."""
    return git_tools.push()


@tool(risk=ASK)
async def github_create_pr(title: str, body: str, head: str) -> str:
    """Open a GitHub pull request.

    Args:
        title: Pull request title.
        body: Pull request description.
        head: The branch with the changes.
    """
    return await git_tools.create_pr(title, body, head)


# ── Screen ─────────────────────────────────────────────────────────────────────

def _grab_screen_png() -> tuple[Optional[bytes], str]:
    """Return (png_bytes, note). png_bytes is None if the screenshot failed."""
    shot = pc_control.take_screenshot()
    if not shot.startswith("data:image"):
        return None, shot
    return base64.b64decode(shot.split(",", 1)[1]), "ok"


@tool(risk=ASK)
def take_screenshot() -> str:
    """Save a screenshot to disk and return where it was saved. Use look_at_screen to understand what is on screen."""
    png, note = _grab_screen_png()
    if png is None:
        return note
    # The old version pasted the whole image into the chat as text, which could burn
    # hundreds of thousands of tokens in one call. Now it goes to a file.
    folder = config.PROJECT_ROOT / "data" / "screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"screen-{time.strftime('%Y%m%d-%H%M%S')}.png"
    path.write_bytes(png)
    return f"Screenshot saved to {path} ({len(png):,} bytes)."


@tool(risk=ASK)
async def look_at_screen(question: str = "Describe this screen. List visible buttons, text, and their approximate positions.") -> str:
    """Take a screenshot and have a vision model describe it or answer a question about it.

    Args:
        question: What you want to know about the screen.
    """
    import asyncio
    from tools import vision
    shot = await asyncio.to_thread(pc_control.take_screenshot)
    if not shot.startswith("data:image"):
        return shot
    return await vision.analyze_image(shot, question=question)


# ── Mouse, keyboard, windows, clipboard ────────────────────────────────────────

@tool(risk=ASK)
def mouse_click(x: int, y: int, button: Literal["left", "right", "middle"] = "left", clicks: int = 1) -> str:
    """Click the mouse at screen coordinates.

    Args:
        x: Horizontal position in pixels from the left edge.
        y: Vertical position in pixels from the top edge.
        button: Which mouse button.
        clicks: Number of clicks, 2 for a double click.
    """
    return pc_control.mouse_click(x, y, button, clicks)


@tool(risk=ASK)
def type_text(text: str) -> str:
    """Type text with the keyboard into whatever has focus.

    Args:
        text: The text to type.
    """
    return pc_control.type_text(text)


@tool(risk=ASK)
def press_key(key: str) -> str:
    """Press a key or shortcut, e.g. 'enter', 'ctrl+c', 'alt+F4'.

    Args:
        key: The key or plus separated combination.
    """
    return pc_control.press_key(key)


@tool(risk=ASK)
def get_clipboard() -> str:
    """Read the clipboard. It can hold private data, so this needs approval."""
    return pc_control.get_clipboard()


@tool(risk=ASK)
def set_clipboard(text: str) -> str:
    """Replace the clipboard contents.

    Args:
        text: The new clipboard text.
    """
    return pc_control.set_clipboard(text)


@tool(risk=SAFE)
def list_windows() -> str:
    """List the open windows."""
    return pc_control.list_windows()


@tool(risk=SAFE)
def focus_window(title_substr: str) -> str:
    """Bring a window to the front.

    Args:
        title_substr: Part of the window title.
    """
    return pc_control.focus_window(title_substr)


@tool(risk=ASK)
def open_app(app: str) -> str:
    """Launch an application.

    Args:
        app: Application name or path.
    """
    return pc_control.open_app(app)


# ── Skills ─────────────────────────────────────────────────────────────────────

@tool(risk=SAFE)
def skill_list() -> str:
    """List all installed skills."""
    return skills_mod.list_skills()


@tool(risk=SAFE)
def skill_search(query: str) -> str:
    """Search installed skills by keyword.

    Args:
        query: Keyword to look for.
    """
    return skills_mod.search_skills(query)


@tool(risk=ASK)
def skill_install(name: str, version: str = "") -> str:
    """Install a skill from clawhub.com. Skills can include scripts, so this needs approval.

    Args:
        name: Skill name, e.g. 'notion' or 'github'.
        version: Specific version. Omit for the latest.
    """
    return skills_mod.install_skill(name, version)


@tool(risk=ASK)
def skill_update(name: str = "", force: bool = False) -> str:
    """Update one skill, or all skills, from clawhub.

    Args:
        name: Skill to update. Omit to update all.
        force: Overwrite local changes.
    """
    return skills_mod.update_skill(name, force)


# ── Integrations ───────────────────────────────────────────────────────────────
# Importing registers their tools. Each is inert unless its integration is in use.
from tools import paperclip_tools  # noqa: E402,F401
