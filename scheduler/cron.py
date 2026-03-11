"""
Cron scheduler — run agent tasks on a schedule.
Inspired by OpenClaw's cron job system.

Configure via CRON_JOBS env var (JSON array):
  [
    {"schedule": "0 9 * * *", "message": "Good morning! What's on my calendar today?"},
    {"schedule": "*/30 * * * *", "message": "Quick status check — any issues?"}
  ]

Or add jobs programmatically via add_job().
"""
from __future__ import annotations
import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

import config
from core import agent

log = logging.getLogger(__name__)


@dataclass
class CronJob:
    schedule: str          # cron expression: "minute hour day month weekday"
    message: str           # message to send to the agent
    callback: Optional[Callable[[str], None]] = None  # optional result handler
    name: str = ""


_jobs: list[CronJob] = []


def add_job(schedule: str, message: str, callback=None, name: str = "") -> CronJob:
    """Register a new cron job."""
    job = CronJob(schedule=schedule, message=message, callback=callback, name=name or message[:40])
    _jobs.append(job)
    return job


def _load_from_config() -> None:
    """Load cron jobs from CRON_JOBS_JSON env var."""
    try:
        jobs = json.loads(config.CRON_JOBS_JSON)
        for j in jobs:
            add_job(j["schedule"], j["message"], name=j.get("name", ""))
        if jobs:
            log.info("Loaded %d cron jobs from config", len(jobs))
    except Exception as e:
        log.warning("Failed to parse CRON_JOBS: %s", e)


def _next_run_seconds(schedule: str) -> float:
    """
    Return seconds until the next execution of the cron schedule.
    Uses croniter if available, otherwise simple 60s interval.
    """
    try:
        from croniter import croniter
        from datetime import datetime
        ci = croniter(schedule, datetime.now())
        nxt = ci.get_next(float)
        import time
        return max(0.0, nxt - time.time())
    except ImportError:
        log.debug("croniter not installed — defaulting to 60s interval")
        return 60.0
    except Exception as e:
        log.warning("Invalid cron schedule %r: %s — defaulting to 60s", schedule, e)
        return 60.0


async def _run_job(job: CronJob) -> None:
    """Execute a single cron job."""
    log.info("Cron: running job %r", job.name)
    try:
        result = await agent.run(job.message, source="cron")
        log.info("Cron: job %r completed", job.name)
        if job.callback:
            try:
                job.callback(result)
            except Exception as e:
                log.error("Cron job callback error: %s", e)
    except Exception as e:
        log.error("Cron job %r failed: %s", job.name, e)


async def _job_loop(job: CronJob) -> None:
    """Run a job on its schedule forever."""
    while True:
        wait = _next_run_seconds(job.schedule)
        log.debug("Cron: job %r next run in %.0fs", job.name, wait)
        await asyncio.sleep(wait)
        await _run_job(job)


async def run() -> None:
    """Start the cron scheduler."""
    if not config.CRON_ENABLED:
        log.info("Cron scheduler disabled")
        return

    _load_from_config()

    if not _jobs:
        log.info("No cron jobs configured")
        return

    log.info("Starting cron scheduler with %d jobs", len(_jobs))
    tasks = [asyncio.create_task(_job_loop(job)) for job in _jobs]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
