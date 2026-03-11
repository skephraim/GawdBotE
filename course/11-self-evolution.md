# Lesson 11 — Self-Evolution

## The Idea

Most software is static — you write it, deploy it, it stays that way until you update it. GawdBotE can update itself. You tell it what to improve, and it reads its own source code, makes the change, runs a syntax check, commits to a git branch, and opens a GitHub pull request for you to review.

This is called **self-evolution**, and it uses all the other systems you've learned: the agentic loop, file tools, git tools, and the self-aware architecture documented in `CLAUDE.md`.

---

## Triggering Self-Evolution

```bash
# Via Telegram
/evolve add a tool that tells me my public IP address

# Via Discord
!evolve add rate limiting to the webhook server

# Via voice
"Hey Jarvis, improve yourself — add a DuckDuckGo news search endpoint"

# Via CLI
gawdbote evolve "add a tool that converts text to morse code"
```

---

## What Happens Under the Hood

The flow is in `core/self_evolve.py`:

```python
async def evolve(request: str, source: str = "user") -> str:
    evolve_prompt = (
        f"SELF-EVOLUTION REQUEST:\n{request}\n\n"
        f"System instructions for this task:\n{EVOLVE_SYSTEM}\n\n"
        "Begin by reading CLAUDE.md, then proceed with the changes."
    )
    result = await agent.run(evolve_prompt, source=f"evolve/{source}", max_rounds=20)
    return result
```

It's just a specially-crafted message sent to the agent with a higher `max_rounds` limit (20 instead of 10, since code changes take more steps).

The `EVOLVE_SYSTEM` instructions tell the agent exactly what to do:

```
1. Read CLAUDE.md to understand the architecture
2. Read relevant source files before modifying them
3. Make the smallest correct change that fulfills the request
4. Verify syntax: python -m py_compile <file>
5. Use git_create_branch to create a branch named evolve-YYYYMMDD-HHMMSS
6. Commit all changed files
7. Push the branch
8. Create a GitHub PR with the changes described
```

---

## A Concrete Example

Request: "add a tool that tells me my public IP address"

The agent would:

1. Call `read_file("CLAUDE.md")` — understand the architecture
2. Call `read_file("tools/code_tools.py")` — see how tools are structured
3. Call `read_file("core/agent.py")` — see the TOOLS list and _dispatch
4. Call `write_file("tools/code_tools.py", ...)` — add the function:
   ```python
   async def get_public_ip() -> str:
       async with aiohttp.ClientSession() as s:
           async with s.get("https://api.ipify.org?format=json") as r:
               data = await r.json()
               return f"Public IP: {data['ip']}"
   ```
5. Call `write_file("core/agent.py", ...)` — add tool definition and dispatch
6. Call `run_command("python -m py_compile tools/code_tools.py")` — verify syntax
7. Call `run_command("python -m py_compile core/agent.py")` — verify syntax
8. Call `git_create_branch("evolve-20260311-143022")`
9. Call `git_commit("Add get_public_ip tool", ["tools/code_tools.py", "core/agent.py"])`
10. Call `git_push()`
11. Call `github_create_pr("Add get_public_ip tool", "...", "evolve-20260311-143022")`

You then get a GitHub PR to review before merging. Nothing is auto-merged unless `SELF_EVOLVE_AUTO_MERGE=true`.

---

## Why CLAUDE.md is Critical

`CLAUDE.md` is a plain text file in the project root that explains the architecture to the agent. Without it, the agent would have to guess where to add things.

With it, the agent knows:
- "Tools go in `tools/` and also need a definition in `core/agent.py`"
- "New interfaces go in `interfaces/` and get added to `main.py`"
- "Never modify `.env` or `data/`"
- "Run py_compile before committing"

This is **self-documentation for AI** — the same file serves as docs for humans AND as context for the agent when it modifies itself.

**This is a new kind of software artifact.** Your project now has docs that tell *humans* and *the AI* how the system works.

---

## The Safety Net: Pull Requests

Self-evolution creates a PR instead of directly merging because:

1. **Review** — you can see exactly what changed before it goes live
2. **Rollback** — if something breaks, you just don't merge
3. **Audit trail** — every self-improvement is documented in git history

The default is `SELF_EVOLVE_AUTO_MERGE=false`. You can set it to `true` for fully autonomous operation — but think carefully before doing that.

---

## Limits of Self-Evolution

Self-evolution is impressive but not magic. It works well for:
- Adding new tools
- Tweaking existing behavior
- Adding endpoints or commands
- Small refactors

It struggles with:
- Large architectural changes
- Changes requiring deep understanding of complex state
- Anything that needs external credentials it doesn't have

Think of it as a junior developer who knows the codebase and can implement clearly-described tasks — but needs a PR review before merging.

---

## Exercise

Try a self-evolution request that's small and concrete:

```
/evolve add a tool called "count_words" that counts the words in a given text string
```

Watch `gawdbote logs` as it reads files, makes changes, and creates a PR. Then check GitHub to see the PR it opened. Review the diff — is it correct? Did it put the function in the right file? Did it add the tool definition and dispatch correctly?

If the PR looks good, merge it. If not, close it and try a more specific request.

---

## Key Takeaways

- Self-evolution sends a specially-crafted message to the agent with code-editing instructions
- The agent uses file tools, git tools, and `py_compile` to safely make changes
- `CLAUDE.md` is the architecture document that guides the agent's edits — keep it up to date
- Changes go through a **PR review** before merging (unless auto-merge is enabled)
- This pattern — an agent that reads and edits its own source — is the foundation of fully autonomous software development
