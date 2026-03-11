# Lesson 03 — The Agentic Loop

This is the heart of GawdBotE. Everything else — voice, Telegram, memory, skills — is just delivery and storage. The agentic loop is what makes it an *agent* instead of a chatbot.

---

## The Loop in Plain English

1. User sends a message
2. We send the message (plus conversation history) to the LLM, along with a list of available tools
3. The LLM either:
   - **Responds directly** → we're done, return the answer
   - **Calls one or more tools** → we run those tools, add the results to the conversation, go back to step 2
4. Repeat until the LLM gives a final text response

That's it. The "intelligence" is entirely in the LLM deciding when to call tools and what to do with the results.

---

## Visualizing a Multi-Turn Tool Call

```
User: "Take a screenshot and tell me what's on screen"

Round 1:
  → Send to LLM: [user message] + tools list
  ← LLM: "I'll take a screenshot" + tool_call: take_screenshot()

  → Run take_screenshot() → returns base64 PNG string
  → Add tool result to messages

Round 2:
  → Send to LLM: [user msg, assistant tool call, tool result]
  ← LLM: "Your screen shows Firefox open to google.com, with a Terminal in the background"

Done. Return final response.
```

---

## Defining a Tool

A tool definition tells the LLM what the tool is called, what it does, and what arguments it takes. This goes in the `TOOLS` list in `core/agent.py`:

```python
{
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for current information",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query"
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of results (default 5)",
                    "default": 5
                }
            },
            "required": ["query"]   # only query is required
        }
    }
}
```

**The description is everything.** The LLM reads the description to decide *when* to call the tool. A bad description means the tool never gets used (or gets used wrong).

---

## Dispatching Tool Calls

When the LLM calls a tool, it sends back JSON like this:

```json
{
  "tool_calls": [{
    "id": "call_abc123",
    "function": {
      "name": "web_search",
      "arguments": "{\"query\": \"current weather in Austin\", \"max_results\": 3}"
    }
  }]
}
```

Your code receives this and runs the actual function. In GawdBotE, this is the `_dispatch()` function in `core/agent.py`:

```python
async def _dispatch(name: str, args: dict) -> str:
    if name == "web_search":
        return await web_search.search(args["query"], max_results=args.get("max_results", 5))
    if name == "take_screenshot":
        return pc_control.take_screenshot()
    if name == "read_file":
        return code_tools.read_file(**args)
    # ... 20+ more tools ...
    return f"Unknown tool: {name}"
```

Simple if/elif chain. Each branch calls the real Python function and returns a string result that goes back to the LLM.

---

## Parallel Tool Calls

Modern LLMs can call multiple tools in one shot. GawdBotE runs them in parallel using `asyncio.gather`:

```python
# The LLM might call: take_screenshot AND memory_search at the same time
tool_tasks = []
for tc in resp["tool_calls"]:
    args = json.loads(tc["arguments"])
    tool_tasks.append((tc["id"], tc["name"], _dispatch(tc["name"], args)))

# Run all tool calls simultaneously
results = await asyncio.gather(*[t[2] for t in tool_tasks], return_exceptions=True)
```

If the LLM asks for a screenshot AND searches memory at the same time, both happen in parallel. This is much faster than running them one at a time.

---

## The Full Loop — Real Code

Here's the actual `run()` function from `core/agent.py`, with added comments:

```python
async def run(user_message, history=None, source="user", max_rounds=10):
    messages = list(history or [])
    messages.append({"role": "user", "content": user_message})

    # Inject relevant skill instructions into the system prompt
    skill_context = skills_mod.build_skill_context(user_message)
    effective_system = SYSTEM_PROMPT + skill_context if skill_context else SYSTEM_PROMPT

    for round_num in range(max_rounds):        # safety cap — never infinite
        resp = await llm.chat(messages, tools=TOOLS, system=effective_system)

        if not resp["tool_calls"]:             # LLM gave a final answer
            final = resp["content"]
            await memory.store(f"[{source}] User: {user_message}\nAssistant: {final}")
            return final                       # ← we're done

        # LLM wants to call tools — add its "thinking" to the conversation
        messages.append({
            "role": "assistant",
            "content": resp["content"] or None,
            "tool_calls": [...]
        })

        # Run all tool calls (potentially in parallel)
        results = await asyncio.gather(*[_dispatch(tc["name"], ...) for tc in resp["tool_calls"]])

        # Add each tool result back to the conversation
        for (call_id, name, _), result in zip(tool_tasks, results):
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": str(result)
            })
        # Loop back — LLM sees the tool results and decides what to do next

    return "Max tool-call rounds reached."
```

The `max_rounds=10` cap is important — without it, a confused LLM could loop forever. In practice, most tasks complete in 1-3 rounds.

---

## Why This is Powerful

The LLM isn't just pattern-matching to generate text — it's doing multi-step **reasoning**:

> "The user wants a screenshot analyzed. I should first take a screenshot, then analyze it. But to analyze it I need the vision model. Let me call take_screenshot first, then call analyze_image with what I get back."

That chain of reasoning — deciding what to do, doing it, seeing the result, deciding what to do next — is what makes an agent feel "smart."

---

## Exercise

Look at `core/agent.py`. Add a new toy tool:

1. Add a tool definition to `TOOLS`:
```python
{
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "Get the current date and time",
        "parameters": {"type": "object", "properties": {}}
    }
}
```

2. Add a dispatch case to `_dispatch()`:
```python
if name == "get_time":
    from datetime import datetime
    return datetime.now().strftime("%A, %B %d %Y at %I:%M %p")
```

3. Run `./run.sh chat` and ask "What time is it?" — watch the agent call your new tool.

---

## Key Takeaways

- The agentic loop: send to LLM → get tool calls → run tools → send results → repeat
- Tool definitions are **JSON schemas** — the LLM reads the `description` to know when to use each tool
- `_dispatch()` is the bridge between the LLM's decision and your Python functions
- Tool calls can run in **parallel** with `asyncio.gather`
- A `max_rounds` cap prevents infinite loops
- The whole conversation history (including tool results) is sent on every API call
