"""
GawdBotE core agent — agentic loop with tool calling.
Receives messages from any interface (voice, Telegram, Discord, Slack, webhooks)
and returns a response string.
"""
from __future__ import annotations
import json
import logging
from typing import Optional

import config
from core import llm, memory, skills as skills_mod
from tools import git_tools, code_tools, pc_control, web_search

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are GawdBotE — a powerful, self-improving AI assistant with full system access.

Capabilities:
- Files & shell: read/write files, run commands, edit code
- Git & GitHub: commit, branch, push, create PRs
- PC control: mouse, keyboard, screenshots, open apps, clipboard
- Web search: search the internet for current information
- Memory: store and recall information persistently
- Vision: analyze screenshots and images
- Self-improvement: read and edit your own source code

Guidelines:
- Be direct and concise in responses
- Prefer action over lengthy explanation
- When improving yourself: read CLAUDE.md first, change only what's needed, commit to a branch
- Ask one clarifying question if the request is genuinely ambiguous
- Use web search when you need current information you may not have"""

TOOLS = [
    # ── File / shell ──────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path (relative to project root or absolute)"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file (creates or overwrites)",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command and return its output",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string", "description": "Working directory (optional)"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_project_files",
            "description": "List all project source files",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    # ── Memory ────────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "memory_store",
            "description": "Store a piece of information in persistent memory",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The information to remember"},
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": "Search persistent memory for relevant information",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
    # ── Web search ────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "description": "Number of results (default 5)", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    # ── Git ───────────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show git status of the project",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show git diff",
            "parameters": {
                "type": "object",
                "properties": {"file": {"type": "string", "description": "Optional file path"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "Show recent git commits",
            "parameters": {
                "type": "object",
                "properties": {"n": {"type": "integer", "description": "Number of commits", "default": 10}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "Commit changes to git",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "files": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["message", "files"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_create_branch",
            "description": "Create and switch to a new git branch",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_push",
            "description": "Push current branch to remote",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_create_pr",
            "description": "Create a GitHub pull request",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "head": {"type": "string", "description": "Source branch"},
                },
                "required": ["title", "body", "head"],
            },
        },
    },
    # ── PC control ────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "take_screenshot",
            "description": "Take a screenshot and return it as base64 PNG",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mouse_click",
            "description": "Click the mouse at coordinates",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                    "clicks": {"type": "integer", "default": 1},
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text using the keyboard",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_key",
            "description": "Press a key or keyboard shortcut (e.g. 'ctrl+c', 'enter', 'alt+F4')",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_clipboard",
            "description": "Read the clipboard contents",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_clipboard",
            "description": "Set clipboard contents",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_windows",
            "description": "List open windows",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "focus_window",
            "description": "Focus a window by title substring",
            "parameters": {
                "type": "object",
                "properties": {"title_substr": {"type": "string"}},
                "required": ["title_substr"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Launch an application",
            "parameters": {
                "type": "object",
                "properties": {"app": {"type": "string", "description": "Application name or path"}},
                "required": ["app"],
            },
        },
    },
    # ── Skills ────────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "skill_list",
            "description": "List all installed skills",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_search",
            "description": "Search installed skills by keyword",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_install",
            "description": "Install a skill from clawhub.com by name",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name, e.g. 'notion' or 'github'"},
                    "version": {"type": "string", "description": "Optional version, e.g. '1.2.3'"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_update",
            "description": "Update a skill (or all skills) from clawhub",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name to update, or omit to update all"},
                    "force": {"type": "boolean", "default": False},
                },
            },
        },
    },
]


# ── Tool dispatch ─────────────────────────────────────────────────────────────

async def _dispatch(name: str, args: dict) -> str:
    if name == "read_file":
        return code_tools.read_file(**args)
    if name == "write_file":
        return code_tools.write_file(**args)
    if name == "run_command":
        return await code_tools.run_command(**args)
    if name == "list_project_files":
        return code_tools.list_project_files()

    if name == "memory_store":
        mid = await memory.store(args["content"])
        return f"Stored as memory #{mid}."
    if name == "memory_search":
        results = await memory.search(args["query"])
        if not results:
            return "No relevant memories found."
        lines = [f"[{r['score']:.2f}] {r['content']}" for r in results]
        return "\n".join(lines)

    if name == "web_search":
        return await web_search.search(args["query"], max_results=args.get("max_results", 5))

    if name == "git_status":
        return git_tools.status()
    if name == "git_diff":
        return git_tools.diff(args.get("file"))
    if name == "git_log":
        return git_tools.log(args.get("n", 10))
    if name == "git_commit":
        return git_tools.commit(args["message"], args["files"])
    if name == "git_create_branch":
        return git_tools.create_branch(args["name"])
    if name == "git_push":
        return git_tools.push()
    if name == "github_create_pr":
        import asyncio
        return await git_tools.create_pr(args["title"], args["body"], args["head"])

    if name == "take_screenshot":
        return pc_control.take_screenshot()
    if name == "mouse_click":
        return pc_control.mouse_click(**args)
    if name == "type_text":
        return pc_control.type_text(args["text"])
    if name == "press_key":
        return pc_control.press_key(args["key"])
    if name == "get_clipboard":
        return pc_control.get_clipboard()
    if name == "set_clipboard":
        return pc_control.set_clipboard(args["text"])
    if name == "list_windows":
        return pc_control.list_windows()
    if name == "focus_window":
        return pc_control.focus_window(args["title_substr"])
    if name == "open_app":
        return pc_control.open_app(args["app"])

    if name == "skill_list":
        return skills_mod.list_skills()
    if name == "skill_search":
        return skills_mod.search_skills(args["query"])
    if name == "skill_install":
        return skills_mod.install_skill(args["name"], args.get("version", ""))
    if name == "skill_update":
        return skills_mod.update_skill(args.get("name", ""), args.get("force", False))

    return f"Unknown tool: {name}"


# ── Agentic loop ──────────────────────────────────────────────────────────────

async def run(
    user_message: str,
    history: Optional[list[dict]] = None,
    source: str = "user",
    max_rounds: int = 10,
) -> str:
    """
    Run the agentic loop and return a final text response.
    history: list of prior {role, content} messages for multi-turn context.
    source: where the message came from (telegram, discord, voice, webhook, …)
    """
    messages: list[dict] = list(history or [])
    messages.append({"role": "user", "content": user_message})

    # Inject relevant skill context into the system prompt
    skill_context = skills_mod.build_skill_context(user_message)
    effective_system = SYSTEM_PROMPT + skill_context if skill_context else SYSTEM_PROMPT

    for round_num in range(max_rounds):
        resp = await llm.chat(messages, tools=TOOLS, system=effective_system)

        # If no tool calls, we're done
        if not resp["tool_calls"]:
            final = resp["content"]
            # Store the exchange in memory
            await memory.store(
                f"[{source}] User: {user_message}\nAssistant: {final}",
                source=source,
            )
            return final

        # Append assistant turn with tool calls
        messages.append({
            "role": "assistant",
            "content": resp["content"] or None,
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                }
                for tc in resp["tool_calls"]
            ],
        })

        # Execute all tool calls in parallel
        import asyncio
        tool_tasks = []
        for tc in resp["tool_calls"]:
            try:
                args = json.loads(tc["arguments"])
            except json.JSONDecodeError:
                args = {}
            tool_tasks.append((tc["id"], tc["name"], _dispatch(tc["name"], args)))

        results = await asyncio.gather(*[t[2] for t in tool_tasks], return_exceptions=True)

        for (call_id, name, _), result in zip(tool_tasks, results):
            if isinstance(result, Exception):
                result = f"Tool error: {result}"
            log.debug("Tool %s → %s", name, str(result)[:200])
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": str(result),
            })

    return "Max tool-call rounds reached. Please simplify the request."
