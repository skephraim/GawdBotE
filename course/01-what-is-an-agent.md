# Lesson 01 — What is an AI Agent?

## The Problem with Plain Chatbots

A regular chatbot (like the basic ChatGPT API) does one thing: you send it text, it sends text back. That's it. It can't open a file. It can't run a command. It can't look anything up. It only knows what was in its training data — which has a cutoff date.

```
You: "What files are in my project?"
Chatbot: "I don't have access to your file system."
```

An **AI agent** solves this by giving the LLM **tools** it can call — and letting it decide when to use them.

```
You: "What files are in my project?"
Agent: [calls list_project_files tool] → gets real list → "You have: main.py, config.py, core/agent.py..."
```

---

## The Key Insight: The LLM as a Decision-Maker

The LLM isn't just generating text anymore. It's acting as a **reasoning engine** that decides:

1. Do I know the answer already? → respond directly
2. Do I need more information? → call a tool
3. What did the tool return? → use that to keep going or respond

This is called the **agentic loop** — and it's the core idea behind GawdBotE and every serious AI assistant.

---

## Chatbot vs. Agent — Side by Side

| | Chatbot | Agent |
|---|---|---|
| Can access your files | ✗ | ✓ |
| Can run shell commands | ✗ | ✓ |
| Can browse the web | ✗ | ✓ |
| Can remember past conversations | ✗ (usually) | ✓ |
| Can take actions in the world | ✗ | ✓ |
| Knows today's news | ✗ | ✓ (via search) |
| Can improve its own code | ✗ | ✓ |

---

## What GawdBotE Can Do

GawdBotE is an agent with these tools available:

```
Files & shell    read_file, write_file, run_command
Git & GitHub     git_commit, git_push, github_create_pr
PC control       mouse_click, type_text, take_screenshot, open_app
Web              web_search
Memory           memory_store, memory_search
Vision           analyze_image (sends screenshot to vision LLM)
Self-improvement reads and edits its own source code
```

When you send GawdBotE a message — from Telegram, Discord, voice, or the CLI — it runs the agentic loop, calling whatever tools it needs, until it has an answer.

---

## The Mental Model

Think of GawdBotE as an employee at a computer:

- **You** are the manager giving tasks
- **The LLM** is the employee's brain — it reads, thinks, decides
- **The tools** are the keyboard, mouse, browser, and terminal
- **The memory** is the employee's notebook

You say "book me a flight" — the employee doesn't just say "here's how to book a flight." They open the browser, navigate to the site, fill in the form, and come back to tell you it's done.

---

## Exercise

Open `core/agent.py` in GawdBotE. Find the `TOOLS` list near the top. Count how many tools are defined. For each one, ask yourself: **what real-world action does this represent?**

Then look at the `SYSTEM_PROMPT` string. Notice how it tells the LLM *who it is* and *what it can do*. This is how you set the agent's personality and awareness of its own capabilities.

---

## Key Takeaways

- An **AI agent** = an LLM + tools + a loop that lets it call tools until it has an answer
- The LLM doesn't execute tools — it *decides* when to call them; your code executes them
- GawdBotE has ~25 tools covering files, git, PC control, web, memory, and self-improvement
- The same core concept powers GitHub Copilot, Claude Code, and every serious AI assistant
