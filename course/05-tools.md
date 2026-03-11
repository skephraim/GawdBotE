# Lesson 05 — Tools: Files, Shell & PC Control

## What a Tool Actually Is

A tool is just a **Python function** that returns a string. That's it. The LLM asks for it by name, your code runs it, and the string result goes back to the LLM.

```python
def read_file(path: str) -> str:
    target = Path(path)
    if not target.exists():
        return f"File not found: {path}"
    return target.read_text(encoding="utf-8")
```

The LLM calls `read_file(path="config.py")`. Your code runs this function. The LLM gets back the file contents as a string. Done.

---

## Three Categories of Tools in GawdBotE

### 1. File & Shell Tools (`tools/code_tools.py`)

The most fundamental tools — read files, write files, run commands.

```python
def read_file(path: str) -> str:
    target = Path(path) if Path(path).is_absolute() else ROOT / path
    return target.read_text(encoding="utf-8")

def write_file(path: str, content: str) -> str:
    target = Path(path) if Path(path).is_absolute() else ROOT / path
    if any(p in str(target) for p in {".env", ".git"}):  # protect sensitive files
        return f"Refused: {path} is protected."
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Written: {target}"

async def run_command(command: str, cwd=None) -> str:
    proc = await asyncio.create_subprocess_shell(
        command, cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    return stdout.decode().strip() or stderr.decode().strip() or "(no output)"
```

Three things to notice:
1. `read_file` and `write_file` are **synchronous** — they don't need `async` because they're fast disk operations
2. `run_command` is **async** — shell commands can take time, and we don't want to block the event loop
3. `write_file` has a **protection check** — never let the agent overwrite `.env` or `.git`

### 2. Git Tools (`tools/git_tools.py`)

Git operations let the agent commit and push code — essential for the self-evolution feature.

```python
def _run(cmd: list[str], cwd=ROOT) -> str:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        return f"Error: {result.stderr or result.stdout}"
    return result.stdout.strip() or "OK"

def commit(message: str, files: list[str]) -> str:
    for f in files:
        _run(["git", "add", f])
    return _run(["git", "commit", "-m", message])

def create_branch(name: str) -> str:
    return _run(["git", "checkout", "-b", name])

async def create_pr(title: str, body: str, head: str) -> str:
    # Uses GitHub REST API with aiohttp
    url = f"https://api.github.com/repos/{config.GITHUB_REPO}/pulls"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json={...}, headers={...}) as resp:
            data = await resp.json()
            return f"PR created: {data['html_url']}"
```

The git operations use `subprocess.run` (synchronous, fine for quick git calls). The GitHub API call uses `aiohttp` (async HTTP client) since it's a network request.

### 3. PC Control (`tools/pc_control.py`)

This is what makes GawdBotE genuinely powerful — it can *drive your computer*.

```python
def mouse_click(x: int, y: int, button="left", clicks=1) -> str:
    import pyautogui
    pyautogui.click(x, y, button=button, clicks=clicks)
    return f"Clicked {button} at ({x}, {y}) × {clicks}"

def type_text(text: str) -> str:
    import pyautogui
    pyautogui.typewrite(text, interval=0.03)  # 30ms between keystrokes
    return f"Typed: {text}"

def take_screenshot(region=None) -> str:
    import pyautogui
    img = pyautogui.screenshot(region=region)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{encoded}"  # returns base64 string
```

`pyautogui` is the library doing the heavy lifting here. It controls the mouse and keyboard at the OS level. The screenshot is returned as a **base64-encoded PNG string** — which can then be passed to the vision LLM for analysis.

---

## The Protection Pattern

Notice how `write_file` refuses to touch `.env`:

```python
_PROTECTED = {".env", ".git"}

def write_file(path: str, content: str) -> str:
    if any(p in str(target) for p in _PROTECTED):
        return f"Refused: {path} is protected."
```

And `pc_control` checks if control is enabled:

```python
def _check_enabled():
    if not config.PC_CONTROL_ENABLED:
        return "PC control is disabled. Set PC_CONTROL_ENABLED=true in .env"
    return None

def mouse_click(x, y, ...):
    if (e := _check_enabled()): return e  # walrus operator — assign and check
    ...
```

**Design rule:** Every tool that can cause harm should have a guard. If the tool returns an error string, the LLM sees it and can either try something else or tell the user what happened. It never crashes the agent.

---

## Sync vs. Async Tools

When should a tool be `async`?

| Tool type | Async? | Why |
|-----------|--------|-----|
| File read/write | No | Fast, OS-level, doesn't block meaningfully |
| Shell command | Yes | Can take seconds or minutes |
| HTTP request | Yes | Network I/O — always async |
| Mouse/keyboard | No | Instant OS call |
| Screenshot | No | Fast |
| GitHub PR | Yes | HTTP request |

The rule: if it touches a **network** or **takes significant time**, make it async.

In `_dispatch()`, all tools are called with `await` regardless:

```python
tool_tasks.append((tc["id"], tc["name"], _dispatch(tc["name"], args)))
results = await asyncio.gather(*[t[2] for t in tool_tasks])
```

If `_dispatch` calls a sync function like `read_file`, that's fine — it just returns immediately. `asyncio.gather` handles both sync-returning and async-returning coroutines.

---

## Building a New Tool in 3 Steps

Say you want a tool that tells the agent how much disk space is free.

**Step 1:** Write the function in `tools/code_tools.py`:
```python
def disk_usage() -> str:
    import shutil
    total, used, free = shutil.disk_usage("/")
    return f"Disk: {free // (2**30)} GB free of {total // (2**30)} GB total"
```

**Step 2:** Add the tool definition to `TOOLS` in `core/agent.py`:
```python
{
    "type": "function",
    "function": {
        "name": "disk_usage",
        "description": "Check how much disk space is available",
        "parameters": {"type": "object", "properties": {}}
    }
}
```

**Step 3:** Add dispatch to `_dispatch()` in `core/agent.py`:
```python
if name == "disk_usage":
    return code_tools.disk_usage()
```

That's genuinely it. The agent can now use `disk_usage` whenever someone asks "how much space do I have?"

---

## Key Takeaways

- A tool is just a **Python function that returns a string**
- Three tool families: files/shell, git/GitHub, PC control (mouse/keyboard/screenshots)
- Async tools for network/slow operations; sync tools are fine for fast local operations
- Always add **protection guards** to tools that can cause harm
- Adding a new tool = 1 function + 1 JSON definition + 1 dispatch line
- The screenshot tool returns base64 — which the vision LLM can then analyze
