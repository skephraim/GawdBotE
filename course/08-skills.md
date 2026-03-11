# Lesson 08 — Skills

## The Problem: The Agent Doesn't Know Everything

GawdBotE has a strong system prompt that explains its general capabilities. But if you ask it to use the Notion API, it doesn't know the exact endpoints. If you ask it to control tmux sessions, it doesn't know the right flags. If you ask about the 1Password CLI, it doesn't know the auth flow.

You could dump all of this into the system prompt — but that would use thousands of tokens on every single message, most of it irrelevant.

**Skills** solve this: inject only the instructions that are relevant to *this specific message*.

---

## What a Skill Is

A skill is a **markdown file** with YAML frontmatter. The frontmatter describes the skill; the markdown body contains the instructions that get injected into the agent's system prompt when the skill is relevant.

```markdown
---
name: notion
description: Notion API for creating and managing pages, databases, and blocks.
---

# notion

Use the Notion API to create/read/update pages, data sources (databases), and blocks.

## Setup
1. Create an integration at https://notion.so/my-integrations
2. Copy the API key (starts with `ntn_`)
...

## Common Operations

**Search for pages:**
curl -X POST "https://api.notion.com/v1/search" \
  -H "Authorization: Bearer $NOTION_KEY" \
  ...
```

The agent sees this when you ask "add this to my Notion database" — but not when you ask "what's the weather?"

---

## How Skills Are Loaded

`core/skills.py` reads all the SKILL.md files at startup and caches them:

```python
def load_all() -> dict[str, Skill]:
    skills = {}
    for skill_file in SKILLS_DIR.rglob("SKILL.md"):
        text = skill_file.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(text)   # split YAML header from content
        name = meta.get("name") or skill_file.parent.name
        description = meta.get("description", "")
        skills[name] = Skill(name=name, description=description, content=body, path=skill_file)
    return skills
```

52 skills are loaded. Each one has a `name`, `description`, and `content` (the full markdown body).

---

## How Relevance Is Determined

When a user sends a message, GawdBotE checks which skills are relevant using **keyword matching**:

```python
def find_relevant(message: str, max_skills=3) -> list[Skill]:
    message_lower = message.lower()
    words = set(re.findall(r'\w+', message_lower))   # set of words in the message

    scored = []
    for skill in skills.values():
        skill_words = set(re.findall(r'\w+',
            (skill.name + " " + skill.description).lower()
        ))
        overlap = words & skill_words           # words in common
        if overlap:
            score = len(overlap)
            # Big bonus if the skill name is mentioned directly
            if skill.name.lower() in message_lower:
                score += 5
            scored.append((score, skill))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:max_skills]]
```

Example: message = "add a task to my Notion database"
- Words in message: {add, a, task, to, my, notion, database}
- Notion skill description: "Notion API for creating and managing pages, databases..."
- Overlap: {notion, database} → score = 2 + 5 (direct name match) = 7

The top 3 scoring skills get their full content injected into the system prompt.

---

## Injection in Action

In `core/agent.py`, before every LLM call:

```python
skill_context = skills_mod.build_skill_context(user_message)
effective_system = SYSTEM_PROMPT + skill_context if skill_context else SYSTEM_PROMPT
```

And `build_skill_context()` assembles the injected content:

```python
def build_skill_context(message: str) -> str:
    relevant = find_relevant(message)
    if not relevant:
        return ""

    parts = ["\n## Available Skills\n"]
    for skill in relevant:
        parts.append(f"### Skill: {skill.name}\n{skill.content}\n")

    return "\n".join(parts)
```

So the system prompt becomes:

```
You are GawdBotE...
[base system prompt]

## Available Skills

### Skill: notion
[full notion skill content — exact API endpoints, auth flow, etc.]

### Skill: github
[full github CLI cheat sheet]
```

The LLM now has everything it needs to actually use Notion or GitHub correctly — but only when those skills are relevant.

---

## The 52 Built-in Skills

GawdBotE includes all the skills from the OpenClaw project:

| Category | Skills |
|----------|--------|
| Note-taking | notion, obsidian, bear-notes, apple-notes |
| Dev tools | github, gh-issues, coding-agent, tmux |
| Communication | slack, discord, telegram, himalaya (email) |
| Productivity | trello, apple-reminders, things-mac |
| Media | spotify-player, songsee, video-frames |
| Utilities | weather, web search, 1password, summarize |
| AI | gemini, openai-image-gen, openai-whisper, sherpa-onnx-tts |
| System | healthcheck, session-logs, model-usage |
| Meta | clawhub (install more skills), skill-creator |

---

## Installing More Skills from clawhub.com

Skills are community-contributed and available at clawhub.com. The `clawhub` CLI manages them:

```bash
# Install clawhub CLI
npm i -g clawhub

# Search for skills
clawhub search "postgres"

# Install a skill
clawhub install postgres --dir ./skills

# Update all skills
clawhub update --all --dir ./skills

# Or via GawdBotE itself:
gawdbote chat
> Install the postgres skill from clawhub
```

When you install a skill, it drops a `SKILL.md` into the `skills/` directory. GawdBotE reloads skills automatically.

---

## Creating Your Own Skill

Skills are just markdown files. Create `skills/my-api/SKILL.md`:

```markdown
---
name: my-api
description: Interact with My Company's internal API for task management and reporting.
---

# My Company API

Base URL: https://api.mycompany.internal/v2

## Authentication
All requests need: `Authorization: Bearer $MY_API_KEY`

## Common Operations

**List tasks:**
curl -H "Authorization: Bearer $MY_API_KEY" \
  "https://api.mycompany.internal/v2/tasks"

**Create task:**
curl -X POST "https://api.mycompany.internal/v2/tasks" \
  -H "Authorization: Bearer $MY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"title": "...", "assignee": "..."}'

## Notes
- Rate limit: 100 requests/minute
- Pagination: use ?page=N&per_page=50
```

Now when you say "create a task for the deploy", GawdBotE finds this skill, injects it, and knows exactly how to call your API.

---

## Prompt Engineering — What's Really Happening

Skills are an example of a broader concept: **prompt engineering** — crafting the text you feed to an LLM to get better results.

The three key prompt engineering techniques GawdBotE uses:

1. **System prompt** — sets personality and general capabilities
2. **Skill injection** — adds specific how-to knowledge dynamically
3. **Memory retrieval** — adds relevant past context

Together they give the LLM the right information at the right time without wasting tokens on irrelevant content.

---

## Exercise

Create a skill for something you use. Make a file at `skills/my-skill/SKILL.md`. Give it a name and description in the frontmatter, then write a few real commands or API calls.

Then ask GawdBotE to use it. Watch `gawdbote logs` to see the skill get injected into the system prompt.

---

## Key Takeaways

- Skills are **markdown files** that get injected into the system prompt when relevant
- Relevance is determined by **keyword overlap** between the message and skill description
- Only 1-3 skills are injected per message — no wasted tokens
- GawdBotE ships with 52 skills from the OpenClaw project
- New skills can be installed from clawhub.com or created by anyone
- This pattern (dynamic context injection) is a core prompt engineering technique
