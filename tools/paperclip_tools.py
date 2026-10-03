"""
Tools the agent uses while working a Paperclip task.

They only act on the ONE task this run checked out (core.paperclip.current_task), so the
model cannot reach into other tasks by passing an id. Outside a Paperclip heartbeat they
do nothing and say so.

Risk:
  paperclip_comment, paperclip_finish   SAFE: reporting on the agent's own assigned task
                                        is the job, Paperclip governs and logs it, and
                                        asking you to approve every status note would
                                        make unattended work impossible.
  paperclip_create_subtask              ASK: assigning work wakes other agents, and other
                                        agents spend money.
"""
from __future__ import annotations

from typing import Literal, Optional

from core import paperclip
from core.registry import ASK, SAFE, tool

NOT_IN_TASK = "Not working on a Paperclip task right now, so there is nothing to update."


@tool(risk=SAFE)
async def paperclip_comment(body: str) -> str:
    """Post a progress note on the Paperclip task you are working on.

    Args:
        body: The note, in markdown. Say what changed and what comes next.
    """
    task = paperclip.current_task.get()
    if task is None:
        return NOT_IN_TASK
    await task.client.add_comment(task.issue_id, body)
    return "Comment posted."


@tool(risk=SAFE)
async def paperclip_finish(status: Literal["done", "in_review", "blocked"], comment: str) -> str:
    """Finish your work on the current Paperclip task and report the result. Call this once, at the end.

    Args:
        status: done if complete, in_review if it needs a reviewer, blocked if you cannot continue.
        comment: What you did and why. If blocked, what is blocking you and who can unblock it.
    """
    task = paperclip.current_task.get()
    if task is None:
        return NOT_IN_TASK
    if status not in paperclip.FINISH_STATUSES:
        return f"Tool error: status must be one of {paperclip.FINISH_STATUSES}."
    if not comment.strip():
        return "Tool error: a comment is required so people can see what happened."
    await task.client.update(task.issue_id, status, comment)
    task.finished = status
    return f"Task marked {status}."


@tool(risk=ASK)
async def paperclip_create_subtask(title: str, description: str, assignee_agent_id: Optional[str] = None) -> str:
    """Create a subtask under the current Paperclip task, optionally assigned to another agent.

    Args:
        title: Short title for the subtask.
        description: What needs doing and how to know it is done.
        assignee_agent_id: Agent id or shortname to assign it to. Omit to leave it unassigned.
    """
    task = paperclip.current_task.get()
    if task is None:
        return NOT_IN_TASK
    goal = task.issue.get("goalId") or (task.issue.get("goal") or {}).get("id")
    created = await task.client.create_issue(
        task.company_id, title=title, description=description, status="todo",
        assigneeAgentId=assignee_agent_id, parentId=task.issue_id, goalId=goal,
        projectId=task.issue.get("projectId"),
    )
    new_id = created.get("id") if isinstance(created, dict) else None
    return f"Subtask created{f' with id {new_id}' if new_id else ''}."
