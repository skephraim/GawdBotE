"""
Phase 1 tests: tool registry, event log, approval gate, and the agent loop that ties them together.
Run from the project root:  python -m pytest tests -v
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Literal, Optional

import pytest

import config
from core import agent, approvals, events, memory, registry
from core.context import RunContext


# ── Helpers ────────────────────────────────────────────────────────────────────

@pytest.fixture
def scratch_tools():
    """Register temporary tools for a test, then remove them."""
    before = set(registry.TOOLS)
    yield
    for name in set(registry.TOOLS) - before:
        del registry.TOOLS[name]


def run(coro):
    return asyncio.run(coro)


class FakeBrain:
    """Plays back scripted LLM replies and records what the agent sent each round."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen: list[list[dict]] = []

    async def chat(self, messages, tools=None, system="", **kw):
        self.seen.append(json.loads(json.dumps(messages)))
        return self.replies.pop(0)


def reply(text="", calls=()):
    return {
        "content": text,
        "tool_calls": [{"id": f"c{i}", "name": n, "arguments": json.dumps(a)} for i, (n, a) in enumerate(calls)],
        "finish_reason": "tool_calls" if calls else "stop",
        "provider": "fake",
    }


@pytest.fixture
def wired(monkeypatch, scratch_tools):
    """An agent with a fake brain and two probe tools: one SAFE, one ASK."""
    ran: list[tuple[str, dict]] = []

    @registry.tool(risk=registry.SAFE)
    def probe_read(x: str) -> str:
        """Safe probe."""
        ran.append(("probe_read", {"x": x}))
        return f"read {x}"

    @registry.tool(risk=registry.ASK)
    def probe_write(x: str) -> str:
        """Risky probe."""
        ran.append(("probe_write", {"x": x}))
        return f"wrote {x}"

    async def no_store(*a, **k):
        return 1

    monkeypatch.setattr(memory, "store", no_store)

    def install(replies):
        brain = FakeBrain(replies)
        monkeypatch.setattr(agent.llm, "chat", brain.chat)
        return brain

    return install, ran


def tool_messages(brain) -> list[str]:
    return [m["content"] for m in brain.seen[-1] if m["role"] == "tool"]


# ── Registry: schema generation ────────────────────────────────────────────────

def test_schema_is_built_from_type_hints_and_docstring(scratch_tools):
    @registry.tool(risk=registry.SAFE)
    def sample(name: str, count: int, ratio: float, flag: bool, tags: list[str],
               mode: Literal["fast", "slow"] = "fast", note: Optional[str] = None, limit: int = 5) -> str:
        """Do a sample thing.

        Args:
            name: Who to greet.
            count: How many times,
                spread over two lines.
        """
        return "ok"

    t = registry.get("sample")
    p = t.parameters
    assert t.description == "Do a sample thing."
    assert p["required"] == ["name", "count", "ratio", "flag", "tags"]
    assert p["properties"]["name"] == {"type": "string", "description": "Who to greet."}
    assert p["properties"]["count"]["description"] == "How many times, spread over two lines."
    assert p["properties"]["ratio"]["type"] == "number"
    assert p["properties"]["flag"]["type"] == "boolean"
    assert p["properties"]["tags"] == {"type": "array", "items": {"type": "string"}}
    assert p["properties"]["mode"] == {"type": "string", "enum": ["fast", "slow"], "default": "fast"}
    assert p["properties"]["note"] == {"type": "string"}          # Optional unwrapped, None default omitted
    assert p["properties"]["limit"]["default"] == 5


def test_duplicate_tool_names_are_rejected(scratch_tools):
    @registry.tool(risk=registry.SAFE)
    def dupe() -> str:
        """First."""
    with pytest.raises(ValueError):
        @registry.tool(risk=registry.SAFE, name="dupe")
        def dupe2() -> str:
            """Second."""


def test_tools_without_docstrings_are_rejected(scratch_tools):
    with pytest.raises(ValueError):
        @registry.tool(risk=registry.SAFE)
        def silent() -> str:
            return ""


def test_bad_risk_value_is_rejected():
    with pytest.raises(ValueError):
        registry.tool(risk="yolo")


# ── Registry: risk tiers fail closed ───────────────────────────────────────────

def test_default_risk_is_ask(scratch_tools):
    @registry.tool()
    def forgot_risk() -> str:
        """Author forgot to set a risk."""
    assert registry.get("forgot_risk").risk_for({}) == registry.ASK


def test_risk_rule_that_crashes_means_ask(scratch_tools):
    @registry.tool(risk=lambda a: 1 / 0)
    def fragile() -> str:
        """Risk rule explodes."""
    assert registry.get("fragile").risk_for({}) == registry.ASK


def test_risk_rule_returning_nonsense_means_ask(scratch_tools):
    @registry.tool(risk=lambda a: "totally-fine")
    def weird() -> str:
        """Risk rule returns junk."""
    assert registry.get("weird").risk_for({}) == registry.ASK


def test_read_file_is_safe_inside_project_and_ask_outside(tmp_path):
    rf = registry.get("read_file")
    assert rf.risk_for({"path": "README.md"}) == registry.SAFE
    assert rf.risk_for({"path": str(config.PROJECT_ROOT / "core" / "agent.py")}) == registry.SAFE
    assert rf.risk_for({"path": str(tmp_path / "notes.txt")}) == registry.ASK
    assert rf.risk_for({"path": "../../etc/passwd"}) == registry.ASK      # escaping the project with ..


def test_dangerous_builtins_require_approval():
    for name in ("write_file", "run_command", "git_commit", "git_push", "github_create_pr", "mouse_click",
                 "type_text", "press_key", "get_clipboard", "set_clipboard", "open_app", "take_screenshot",
                 "look_at_screen", "skill_install", "skill_update"):
        assert registry.get(name).risk_for({}) == registry.ASK, name


def test_all_original_tool_names_still_exist():
    old = {"read_file", "write_file", "run_command", "list_project_files", "memory_store", "memory_search",
           "web_search", "git_status", "git_diff", "git_log", "git_commit", "git_create_branch", "git_push",
           "github_create_pr", "take_screenshot", "mouse_click", "type_text", "press_key", "get_clipboard",
           "set_clipboard", "list_windows", "focus_window", "open_app", "skill_list", "skill_search",
           "skill_install", "skill_update"}
    assert old <= set(registry.TOOLS)


def test_every_builtin_spec_is_well_formed():
    for spec in registry.specs():
        fn = spec["function"]
        assert spec["type"] == "function"
        assert fn["description"]
        assert fn["parameters"]["type"] == "object"
        for req in fn["parameters"].get("required", []):
            assert req in fn["parameters"]["properties"]


# ── Registry: calling ──────────────────────────────────────────────────────────

def test_call_reports_bad_arguments_instead_of_crashing(scratch_tools):
    @registry.tool(risk=registry.SAFE)
    def needs_x(x: str) -> str:
        """Needs x."""
        return x

    assert run(registry.call("needs_x", {})).startswith("Tool error: bad arguments")
    assert run(registry.call("needs_x", {"x": "a", "bogus": 1})).startswith("Tool error: bad arguments")
    assert run(registry.call("needs_x", {"x": "a"})) == "a"
    assert run(registry.call("no_such_tool", {})).startswith("Unknown tool")


def test_sync_tools_run_off_the_event_loop_thread(scratch_tools):
    seen = {}

    @registry.tool(risk=registry.SAFE)
    def where_am_i() -> str:
        """Report thread."""
        seen["thread"] = threading.get_ident()
        return "ok"

    async def go():
        seen["loop"] = threading.get_ident()
        return await registry.call("where_am_i", {})

    run(go())
    assert seen["thread"] != seen["loop"]


def test_huge_results_are_capped(scratch_tools):
    @registry.tool(risk=registry.SAFE)
    def firehose() -> str:
        """Too much output."""
        return "x" * (registry.MAX_RESULT_CHARS + 5000)

    out = run(registry.call("firehose", {}))
    assert len(out) < registry.MAX_RESULT_CHARS + 200
    assert "cut off" in out


# ── Event log ──────────────────────────────────────────────────────────────────

def test_events_round_trip_in_order():
    ctx = RunContext(source="cli")
    events.record(ctx, "run_start", message="hello")
    events.record(ctx, "tool_call", tool="git_status", risk="safe", args={})
    events.record(ctx, "run_end", status="ok", rounds=1, final="done")
    rows = events.session_events(ctx.session_id)
    assert [r["kind"] for r in rows] == ["run_start", "tool_call", "run_end"]
    assert rows[0]["detail"]["message"] == "hello"
    assert rows[0]["channel"] == "cli"


def test_events_mask_secret_looking_fields_at_any_depth():
    ctx = RunContext(source="cli")
    events.record(ctx, "tool_call", tool="x", args={"api_key": "sk-123", "nested": {"Password": "hunter2"}, "path": "a.txt"})
    detail = events.session_events(ctx.session_id)[0]["detail"]
    blob = json.dumps(detail)
    assert "sk-123" not in blob and "hunter2" not in blob
    assert detail["args"]["path"] == "a.txt"


def test_events_shorten_long_values():
    ctx = RunContext(source="cli")
    events.record(ctx, "tool_result", tool="x", preview="y" * 10_000)
    preview = events.session_events(ctx.session_id)[0]["detail"]["preview"]
    assert len(preview) < 2_100 and "more chars" in preview


def test_logging_failure_never_raises(monkeypatch, tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("file in the way")
    monkeypatch.setattr(config, "EVENTS_DB", str(blocker / "events.db"))   # parent is a file: cannot work
    events.record(RunContext(source="cli"), "run_start", message="still fine")   # must not raise


def test_sql_injection_in_session_id_is_harmless():
    ctx = RunContext(source="cli")
    events.record(ctx, "run_start", message="keep me")
    assert events.session_events("x' OR '1'='1") == []
    assert len(events.session_events(ctx.session_id)) == 1


def test_recent_sessions_summarizes_each_run():
    a, b = RunContext(source="telegram"), RunContext(source="discord")
    events.record(a, "tool_call", tool="t", risk="ask", args={})
    events.record(a, "approval", tool="t", status="denied")
    events.record(a, "run_end", status="ok", rounds=1, final="x")
    events.record(b, "run_end", status="max_rounds", rounds=10, final="y")
    rows = {r["session_id"]: r for r in events.recent_sessions(10)}
    assert rows[a.session_id]["tool_calls"] == 1
    assert rows[a.session_id]["refused"] == 1
    assert rows[b.session_id]["outcome"] == "max_rounds"
    assert events.last_session_id("telegram") == a.session_id
    assert [r["session_id"] for r in events.recent_sessions(10, channel="discord")] == [b.session_id]


def test_prune_removes_only_old_events(monkeypatch):
    ctx = RunContext(source="cli")
    real_time = events.time.time
    monkeypatch.setattr(events.time, "time", lambda: real_time() - 100 * 86_400)
    events.record(ctx, "run_start", message="ancient")
    monkeypatch.setattr(events.time, "time", real_time)
    events.record(ctx, "run_start", message="fresh")
    assert events.prune(90) == 1
    assert [r["detail"]["message"] for r in events.session_events(ctx.session_id)] == ["fresh"]


# ── Approval gate ──────────────────────────────────────────────────────────────

def test_no_way_to_ask_means_denied():
    ok, reason = run(approvals.request(RunContext(source="webhook"), "run_command", {"command": "ls"}))
    assert ok is False and "nobody can approve" in reason


def test_trusted_tools_skip_the_prompt(monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_TOOLS", {"git_commit"})
    ok, _ = run(approvals.request(RunContext(source="cron"), "git_commit", {}))
    assert ok is True


def _answering(approved: bool, channel: str = "cli"):
    async def ask(req):
        approvals.resolve(req.id, approved, by="tester", channel=channel)
    return ask


def test_owner_approves():
    ok, reason = run(approvals.request(RunContext(source="cli", ask=_answering(True)), "write_file", {"path": "a"}))
    assert ok is True and "tester" in reason


def test_owner_denies():
    ok, reason = run(approvals.request(RunContext(source="cli", ask=_answering(False)), "write_file", {"path": "a"}))
    assert ok is False and "denied" in reason


def test_silence_means_no_and_cleans_up():
    async def ignore(req):
        pass
    ctx = RunContext(source="telegram", ask=ignore)
    ok, reason = run(approvals.request(ctx, "run_command", {"command": "ls"}, timeout=0.05))
    assert ok is False and "did not answer" in reason
    assert approvals.pending() == []
    assert [r["status"] for r in events.session_events(ctx.session_id)] == ["requested", "timeout"]


def test_broken_prompt_delivery_means_no():
    async def broken(req):
        raise ConnectionError("telegram is down")
    ok, reason = run(approvals.request(RunContext(source="telegram", ask=broken), "run_command", {}))
    assert ok is False and "could not be delivered" in reason


def test_only_the_asked_channel_can_answer():
    async def scenario():
        captured = {}

        async def ask(req):
            captured["req"] = req

        task = asyncio.create_task(approvals.request(RunContext(source="telegram", ask=ask), "run_command", {}, timeout=2))
        await asyncio.sleep(0.01)
        rid = captured["req"].id
        wrong = approvals.resolve(rid, True, by="intruder", channel="discord")
        right = approvals.resolve(f"[{rid.upper()}]", False, by="owner", channel="telegram")   # tolerant of formatting
        again = approvals.resolve(rid, True, by="owner", channel="telegram")
        return wrong, right, again, await task

    wrong, right, again, (ok, _) = run(scenario())
    assert "must be answered on telegram" in wrong
    assert right.startswith("Denied")
    assert "No pending approval" in again or "already" in again
    assert ok is False


def test_unknown_request_id():
    assert "No pending approval" in approvals.resolve("ffffff", True, by="x", channel="cli")


def test_unattended_runs_ask_the_owner_channel(monkeypatch):
    monkeypatch.setattr(config, "APPROVAL_OWNER_CHANNEL", "telegram")
    shown = []

    async def owner(req):
        shown.append(req.source)
        approvals.resolve(req.id, True, by="owner-phone", channel="telegram")

    approvals.register_owner_channel("telegram", owner)
    ok, reason = run(approvals.request(RunContext(source="cron"), "git_push", {}))
    assert ok is True and shown == ["cron"] and "owner-phone" in reason


def test_owner_channel_must_match_setting(monkeypatch):
    monkeypatch.setattr(config, "APPROVAL_OWNER_CHANNEL", "telegram")

    async def owner(req):
        approvals.resolve(req.id, True, by="x", channel="discord")

    approvals.register_owner_channel("discord", owner)          # ignored: not the configured channel
    ok, _ = run(approvals.request(RunContext(source="cron"), "git_push", {}))
    assert ok is False


def test_prompts_in_one_run_come_one_at_a_time():
    async def scenario():
        timeline: list[str] = []
        ctx = RunContext(source="telegram")

        async def ask(req):
            timeline.append(f"ask {req.tool}")

            async def answer_later():
                await asyncio.sleep(0.02)
                timeline.append(f"answer {req.tool}")
                approvals.resolve(req.id, True, by="owner", channel="telegram")
            asyncio.create_task(answer_later())

        ctx.ask = ask
        await asyncio.gather(approvals.request(ctx, "a", {}), approvals.request(ctx, "b", {}))
        return timeline

    assert run(scenario()) == ["ask a", "answer a", "ask b", "answer b"]


def test_prompt_shows_full_command_and_masks_secrets():
    loop = asyncio.new_event_loop()
    try:
        req = approvals.ApprovalRequest(
            id="abc123", tool="run_command", args={"command": "rm -rf build", "api_token": "zzz"},
            source="cli", session_id="s", channel="cli", timeout=300, future=loop.create_future(),
        )
        text = req.prompt_text()
    finally:
        loop.close()
    assert "rm -rf build" in text and "zzz" not in text and "abc123" in text


def test_prompt_warns_when_arguments_are_truncated():
    loop = asyncio.new_event_loop()
    try:
        req = approvals.ApprovalRequest(
            id="abc123", tool="write_file", args={"content": "x" * 1_000, "path": "y" * 1_000, "more": "z" * 2_000},
            source="cli", session_id="s", channel="cli", timeout=300, future=loop.create_future(),
        )
        text = req.prompt_text()
    finally:
        loop.close()
    assert "TRUNCATED" in text


# ── The agent loop with everything wired together ──────────────────────────────

def test_safe_tool_runs_without_asking(wired):
    install, ran = wired
    install([reply(calls=[("probe_read", {"x": "1"})]), reply("all done")])
    assert run(agent.run("go", source="webhook")) == "all done"
    assert ran == [("probe_read", {"x": "1"})]


def test_risky_tool_is_blocked_when_nobody_can_approve(wired):
    install, ran = wired
    brain = install([reply(calls=[("probe_write", {"x": "1"})]), reply("could not do it")])
    run(agent.run("go", source="webhook"))
    assert ran == []                                                   # never executed
    assert tool_messages(brain)[0].startswith("NOT RUN: probe_write was not approved")


def test_risky_tool_runs_after_approval(wired):
    install, ran = wired
    install([reply(calls=[("probe_write", {"x": "1"})]), reply("written")])
    assert run(agent.run("go", source="cli", ask=_answering(True), channel="cli")) == "written"
    assert ran == [("probe_write", {"x": "1"})]


def test_risky_tool_does_not_run_after_denial(wired):
    install, ran = wired
    install([reply(calls=[("probe_write", {"x": "1"})]), reply("ok, skipped")])
    run(agent.run("go", source="cli", ask=_answering(False), channel="cli"))
    assert ran == []


def test_unknown_tool_is_reported_and_logged(wired):
    install, ran = wired
    brain = install([reply(calls=[("made_up_tool", {})]), reply("sorry")])
    run(agent.run("go", source="cli"))
    assert tool_messages(brain)[0].startswith("Unknown tool")


def test_full_trace_is_written_in_order(wired):
    install, ran = wired
    install([reply("let me check", calls=[("probe_read", {"x": "a"}), ("probe_write", {"x": "b"})]), reply("finished")])
    ctx = RunContext(source="cli", ask=_answering(True))
    run(agent.run("do both", ctx=ctx))
    rows = events.session_events(ctx.session_id)
    kinds = [r["kind"] for r in rows]
    assert kinds[0] == "run_start" and kinds[-1] == "run_end"
    assert kinds.count("tool_call") == 2 and kinds.count("tool_result") == 2
    assert [r["status"] for r in rows if r["kind"] == "approval"] == ["requested", "approved"]
    assert rows[1]["detail"]["text"] == "let me check" and rows[1]["detail"]["tools"] == ["probe_read", "probe_write"]
    assert all(r["duration_ms"] is not None for r in rows if r["kind"] == "tool_result")
    trace = events.format_session(ctx.session_id)
    assert "approval probe_write: approved by tester" in trace


def test_llm_failure_is_logged_then_raised(wired, monkeypatch):
    async def dead_brain(*a, **k):
        raise RuntimeError("All LLM providers failed")
    monkeypatch.setattr(agent.llm, "chat", dead_brain)
    ctx = RunContext(source="cli")
    with pytest.raises(RuntimeError):
        run(agent.run("hi", ctx=ctx))
    assert events.session_events(ctx.session_id)[-1]["status"] == "error"


def test_max_rounds_is_logged(wired):
    install, ran = wired
    install([reply(calls=[("probe_read", {"x": str(i)})]) for i in range(3)])
    ctx = RunContext(source="cli")
    assert "Max tool-call rounds" in run(agent.run("loop", ctx=ctx, max_rounds=3))
    assert events.session_events(ctx.session_id)[-1]["status"] == "max_rounds"


def test_shell_cannot_read_the_event_database():
    from core import security
    assert security.command_touches_secrets("sqlite3 data/events.db 'select * from events'") is not None
