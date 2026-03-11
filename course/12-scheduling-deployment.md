# Lesson 12 — Scheduling & Deployment

## Cron Jobs — Scheduled Agent Tasks

GawdBotE can run tasks on a schedule using standard cron syntax. This is useful for:
- Morning briefings ("summarize my GitHub issues")
- Periodic check-ins ("any servers down?")
- Weekly summaries ("create a report of this week's activity")

Configure in `.env`:
```bash
CRON_JOBS=[
  {"schedule": "0 9 * * *",  "message": "Good morning! What's on my GitHub today?"},
  {"schedule": "0 18 * * 5", "message": "It's Friday — create a weekly summary."},
  {"schedule": "*/30 * * * *", "message": "Quick status check — anything urgent?"}
]
```

---

## Cron Syntax

Cron expressions have 5 fields: `minute hour day month weekday`

```
0 9 * * *     → 9:00 AM every day
0 18 * * 5    → 6:00 PM every Friday (5 = Friday)
*/30 * * * *  → every 30 minutes
0 9 * * 1-5   → 9:00 AM weekdays only
0 0 1 * *     → midnight on the 1st of each month
```

GawdBotE uses the `croniter` Python library to parse these expressions and calculate when the next run is.

---

## How the Scheduler Works

`scheduler/cron.py` creates one async task per job, each running its own loop:

```python
async def _job_loop(job: CronJob) -> None:
    while True:
        wait = _next_run_seconds(job.schedule)   # how many seconds until next run?
        await asyncio.sleep(wait)                # wait without blocking
        await _run_job(job)                      # run the job

async def _run_job(job: CronJob) -> None:
    result = await agent.run(job.message, source="cron")
    if job.callback:
        job.callback(result)   # e.g. send the result to Telegram
```

The `asyncio.sleep(wait)` is crucial — it yields control back to the event loop while waiting, so all other interfaces (Telegram, Discord, etc.) keep working during the wait.

```python
async def run() -> None:
    _load_from_config()
    tasks = [asyncio.create_task(_job_loop(job)) for job in _jobs]
    await asyncio.gather(*tasks)
```

If you have 3 cron jobs, there are 3 async tasks running simultaneously, each sleeping until their next scheduled time.

---

## systemd — Making It Persistent

`systemd` is Linux's service manager. It starts services at boot, restarts them if they crash, and manages their logs. GawdBotE runs as a **user service** — it starts when you log in, not at system boot (no root required).

The service file `gawdbote.service`:

```ini
[Unit]
Description=GawdBotE — Self-improving AI assistant
After=network-online.target        # wait for network before starting
Wants=network-online.target

[Service]
WorkingDirectory=/home/you/GawdBotE
ExecStart=/home/you/GawdBotE/.venv/bin/python /home/you/GawdBotE/main.py
Restart=always         # restart automatically if it crashes
RestartSec=5           # wait 5 seconds before restarting

EnvironmentFile=/home/you/GawdBotE/.env   # load .env variables
StandardOutput=journal  # send stdout to journald (for 'gawdbote logs')
StandardError=journal

[Install]
WantedBy=default.target   # start when the user session starts
```

Key directives:
- `Restart=always` — the most important one. If GawdBotE crashes (bad API response, network hiccup, anything), systemd brings it back automatically after 5 seconds
- `EnvironmentFile=.../.env` — loads your `.env` file so the process has all your API keys
- `After=network-online.target` — don't start until the network is up (important for bots)
- `StandardOutput=journal` — all `print()` and `logging` output goes to the system journal

---

## install.sh — The One-Shot Installer

`install.sh` does three things:

```bash
# 1. Set up the Python virtualenv
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Install the systemd service
sed "s|%h|$HOME|g" gawdbote.service > ~/.config/systemd/user/gawdbote.service
systemctl --user daemon-reload
systemctl --user enable gawdbote.service   # "enable" = auto-start at login

# 3. Install the 'gawdbote' CLI command
cp gawdbote_cli_script ~/.local/bin/gawdbote
chmod +x ~/.local/bin/gawdbote
```

`systemctl --user enable` tells systemd: "when this user logs in, start this service." The service file gets a symlink in the user's systemd directory.

---

## The gawdbote CLI Command

`~/.local/bin/gawdbote` is a shell script that wraps all the common commands:

```bash
case "$CMD" in
  start)    systemctl --user start gawdbote.service ;;
  stop)     systemctl --user stop gawdbote.service ;;
  restart)  systemctl --user restart gawdbote.service ;;
  status)   systemctl --user status gawdbote.service --no-pager ;;
  logs)     journalctl --user -u gawdbote.service -f ;;
  chat)     cd $REPO && $VENV/bin/python main.py chat ;;
  ask)      curl -s -X POST http://localhost:8080/webhook ... ;;
  evolve)   curl -s -X POST http://localhost:8080/webhook/evolve ... ;;
  doctor)   cd $REPO && $VENV/bin/python main.py doctor ;;
  backup)   cd $REPO && $VENV/bin/python main.py backup ;;
esac
```

The `ask` and `evolve` commands use `curl` to hit the webhook server — so they require GawdBotE to be running. The others use `systemctl` directly.

---

## Viewing Logs

All GawdBotE output goes to `journald`:

```bash
gawdbote logs          # follow live logs
gawdbote logs -n 100   # last 100 lines
journalctl --user -u gawdbote.service --since "1 hour ago"
journalctl --user -u gawdbote.service --since "2026-03-11 09:00"
```

---

## The Full Lifecycle

```bash
# First time setup
cd ~/GawdBotE
./install.sh
nano .env         # add your API keys

# Health check
gawdbote doctor

# Start
gawdbote start
gawdbote status   # verify it's running
gawdbote logs     # watch it start up

# Day to day
gawdbote ask "what's on my github today?"
gawdbote chat     # interactive session

# After changing config
nano .env
gawdbote restart

# After a code update (git pull)
cd ~/GawdBotE && git pull
gawdbote restart

# Maintenance
gawdbote backup create
gawdbote doctor
```

---

## Exercise

Check if GawdBotE is currently enabled as a service:
```bash
systemctl --user is-enabled gawdbote.service
```

Look at recent logs:
```bash
journalctl --user -u gawdbote.service -n 50 --no-pager
```

Add a cron job to your `.env` that runs every minute and logs a message:
```bash
CRON_JOBS=[{"schedule":"* * * * *","message":"Cron test — what time is it right now?"}]
```

Restart GawdBotE and watch the logs. You should see the agent respond to the cron trigger every minute. Remove the job when you're done.

---

## Key Takeaways

- Cron jobs use standard cron syntax; each job runs in its own `asyncio` task loop
- `asyncio.sleep()` waits without blocking — other interfaces keep working
- `systemd` makes GawdBotE persistent: auto-starts at login, auto-restarts on crash
- `EnvironmentFile` loads `.env` so systemd has all your API keys
- `journald` collects all logs — query with `journalctl`
- `install.sh` automates the whole setup: venv + service + CLI command
