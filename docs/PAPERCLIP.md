# Running GawdBot as a Paperclip employee

[Paperclip](https://github.com/paperclipai/paperclip) (MIT licensed) is the company: org
chart, task tickets, goals, budgets, approvals, and a dashboard you can use from your phone.
GawdBot is one employee in that company. Paperclip assigns it tasks and wakes it with a
heartbeat; GawdBot claims a task, does the work with its own tools, and reports back.

```
 Paperclip (Node, localhost:3100)              GawdBot (Python, always running)
 ┌──────────────────────────────┐              ┌─────────────────────────────────┐
 │ task assigned to GawdBot     │              │ python main.py                  │
 │   │                          │   localhost  │  Telegram, Discord, approvals   │
 │   ▼ process adapter runs     │   + secret   │  /paperclip/heartbeat endpoint  │
 │ python main.py heartbeat ────┼─────────────►│   1 identity   2 inbox          │
 │   (prints result for the     │              │   3 pick       4 checkout       │
 │    Paperclip run view)       │◄─────────────┼─  5 context    6 agent works    │
 │                              │  Paperclip   │   7 finish: status + comment    │
 │ task marked done / blocked   │◄─────API─────┤  risky steps → /approve on phone │
 └──────────────────────────────┘              └─────────────────────────────────┘
```

Why the handoff: Paperclip starts a fresh process for every heartbeat. A fresh process has
no Telegram connection, so it could never ask you to approve anything, and every risky
action would be refused. Forwarding to the GawdBot that is already running keeps approvals
working. If GawdBot is not running, the heartbeat still runs, but risky actions are refused.

## 1. Install and start Paperclip

Needs Node.js 24.11 or newer.

```powershell
$env:PAPERCLIP_TELEMETRY_DISABLED = "1"     # Paperclip sends anonymous usage telemetry by default
npx paperclipai@latest onboard --yes
```

Open http://localhost:3100. To keep telemetry off for good, also set
`telemetry.enabled: false` in the Paperclip config file.

## 2. Turn on the integration in GawdBot

In `.env`:

```
PAPERCLIP_ENABLED=true
WEBHOOK_ENABLED=true
WEBHOOK_HOST=127.0.0.1
# WEBHOOK_SECRET must be 16+ characters. Store it in the vault:
#   python main.py secrets set WEBHOOK_SECRET
```

Approvals for Paperclip work go to your Telegram (`APPROVAL_OWNER_CHANNEL=telegram`), so
Telegram should be set up with your user ID in `TELEGRAM_ALLOWED_USER_IDS`, and you must have
pressed Start on your bot once.

Start GawdBot and leave it running: `python main.py`

## 3. Hire GawdBot in Paperclip

Create an agent with the **process** adapter:

| Field | Value |
|---|---|
| command | `C:\path\to\GawdBotE\.venv\Scripts\python.exe main.py heartbeat` |
| cwd | `C:\path\to\GawdBotE` |
| timeoutSec | `1900` (a little more than `PAPERCLIP_HEARTBEAT_TIMEOUT_SECONDS`, default 1800) |

Give it a title and a description of what it is good at, a manager, and a monthly budget.

## 4. Give it work

Assign a task to GawdBot in Paperclip. On the next heartbeat it will:

1. Check its identity and inbox
2. Pick the task it was woken for, then in progress work, then new work (blocked tasks are skipped)
3. Check the task out (if another agent has it, it moves on and never retries)
4. Read the task, its goal, its parent tasks, the plan, and recent comments
5. Do the work with its tools. Risky steps (shell, file writes, git push, creating subtasks
   for other agents) wait for your `/approve` on Telegram
6. Mark the task done, in review, or blocked with a comment. If it forgets, GawdBot posts its
   final answer as a progress comment so nothing is lost

See what it did: `python main.py log` and `python main.py log <session id>`. The session id
is printed in Paperclip's run output.

## Tools available during a Paperclip task

| Tool | Risk | What it does |
|---|---|---|
| `paperclip_comment` | SAFE | Progress note on the current task |
| `paperclip_finish` | SAFE | Final status (done, in_review, blocked) with a comment |
| `paperclip_create_subtask` | ASK | Subtask under the current task, optionally assigned to another agent (other agents spend money) |

They only ever touch the one task this run checked out.

## Security

- Paperclip hands each heartbeat a short lived token. GawdBot never stores it, never prints
  it, never logs it, and only sends it to hosts in `PAPERCLIP_ALLOWED_HOSTS` (localhost by default).
- The heartbeat endpoint is off unless `PAPERCLIP_ENABLED=true`, requires the webhook secret,
  and listens on localhost only.
- Task text can be written by other agents or copied from the web. GawdBot treats it as
  information, and risky tools still need your approval no matter what a task says.

## Not yet verified

This was built against Paperclip's documented API and tested against a fake Paperclip
server. It has not yet run against a live Paperclip install or on Windows.
