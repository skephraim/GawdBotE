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
from core import llm, leads, memory, skills as skills_mod
from tools import git_tools, code_tools, pc_control, web_search, keisha

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
- Keisha lead pipeline: qualify leads, draft outreach, manage the approval queue

Guidelines:
- Be direct and concise in responses
- Prefer action over lengthy explanation
- When improving yourself: read CLAUDE.md first, change only what's needed, commit to a branch
- Ask one clarifying question if the request is genuinely ambiguous
- Use web search when you need current information you may not have
- Keisha NEVER sends outreach on her own: drafts go to the approval queue and
  wait for the owner. Only keisha_send delivers, and only what was approved."""

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
    # ── Keisha: lead pipeline ─────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "keisha_add_lead",
            "description": "Add a business lead to Keisha's pipeline and score it immediately",
            "parameters": {
                "type": "object",
                "properties": {
                    "business_name": {"type": "string"},
                    "contact_name": {"type": "string"},
                    "email": {"type": "string"},
                    "phone": {"type": "string"},
                    "website": {"type": "string", "description": "Leave empty if they have no website"},
                    "niche": {"type": "string", "description": "e.g. plumbing, hvac, roofing"},
                    "city": {"type": "string"},
                    "notes": {"type": "string"},
                    "missed_calls": {"type": "boolean", "description": "Known missed-call problem"},
                },
                "required": ["business_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "keisha_import_csv",
            "description": "Bulk-import leads from a CSV file and score them all",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "default_niche": {"type": "string", "description": "Niche for rows that don't state one"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "keisha_list_leads",
            "description": "List leads in the pipeline, optionally filtered by status or niche",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "new|qualified|rejected|queued|contacted|replied|won|lost|do_not_contact"},
                    "niche": {"type": "string"},
                    "tier": {"type": "string", "enum": ["A", "B"], "description": "A = missed-call evidence (work first), B = target niche + no website"},
                    "limit": {"type": "integer", "default": 25},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "keisha_qualify",
            "description": "Re-score one lead and explain every point of the score",
            "parameters": {
                "type": "object",
                "properties": {"lead_id": {"type": "integer"}},
                "required": ["lead_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "keisha_draft",
            "description": "Draft an outreach message for one lead into the pending approval queue. Does not send.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lead_id": {"type": "integer"},
                    "channel": {"type": "string", "enum": ["email", "sms"], "default": "email"},
                },
                "required": ["lead_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "keisha_run_pipeline",
            "description": "Score all new leads and draft messages for the qualified ones, hottest tier first. Everything lands pending approval; nothing is sent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 20},
                    "tier": {"type": "string", "enum": ["A", "B"], "description": "Restrict to one tier; default drafts both with A first"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "keisha_review",
            "description": "Show the outreach messages waiting for the owner's approval",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 10},
                    "status": {"type": "string", "default": "pending"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "keisha_approve",
            "description": "Approve one queued message for sending. Only the owner may authorize this.",
            "parameters": {
                "type": "object",
                "properties": {"outbox_id": {"type": "integer"}},
                "required": ["outbox_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "keisha_reject",
            "description": "Reject a queued message and return the lead to the qualified pool",
            "parameters": {
                "type": "object",
                "properties": {
                    "outbox_id": {"type": "integer"},
                    "note": {"type": "string"},
                },
                "required": ["outbox_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "keisha_send",
            "description": "Send messages the owner already approved, respecting the daily limit and suppression list. Never sends anything still pending.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 25}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "keisha_block",
            "description": "Add an email address or domain to the permanent do-not-contact list",
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "keisha_report",
            "description": "Snapshot of the whole lead pipeline: counts by status, sent today, targeting",
            "parameters": {"type": "object", "properties": {}},
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

    if name == "keisha_add_lead":
        return keisha.add_lead(**args)
    if name == "keisha_import_csv":
        return keisha.import_csv(args["path"], args.get("default_niche", ""))
    if name == "keisha_list_leads":
        rows = leads.find_leads(status=args.get("status"), niche=args.get("niche"),
                                tier=args.get("tier"), limit=args.get("limit", 25))
        if not rows:
            return "No leads match."
        return "\n".join(
            f"#{r['id']} [tier {r['tier'] or '-'} | {r['status']} {r['score']}] {r['business_name']} "
            f"({r['niche'] or '?'}, {r['city'] or '?'}) {r['email'] or r['phone']}"
            for r in rows
        )
    if name == "keisha_qualify":
        return keisha.qualify(args["lead_id"])
    if name == "keisha_draft":
        return await keisha.draft(args["lead_id"], args.get("channel", "email"))
    if name == "keisha_run_pipeline":
        return await keisha.run_pipeline(limit=args.get("limit", 20), tier=args.get("tier"))
    if name == "keisha_review":
        return keisha.review(limit=args.get("limit", 10), status=args.get("status", "pending"))
    if name == "keisha_approve":
        return keisha.approve(args["outbox_id"])
    if name == "keisha_reject":
        return keisha.reject(args["outbox_id"], args.get("note", ""))
    if name == "keisha_send":
        return await keisha.send_approved(limit=args.get("limit", 25))
    if name == "keisha_block":
        return keisha.do_not_contact(args["value"], args.get("reason", "requested"))
    if name == "keisha_report":
        return keisha.report()

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
