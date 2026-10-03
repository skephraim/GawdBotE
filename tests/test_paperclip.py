"""
Paperclip integration tests, run against an in process fake Paperclip API that speaks the
documented endpoints and records every request it receives.
"""
from __future__ import annotations

import asyncio
import json
import socket

import pytest
from aiohttp import ClientSession, web

import config
from core import agent, approvals, events, memory, paperclip, registry
from cli import heartbeat as hb_cli
from interfaces import webhook_server

TOKEN = "run-token-SECRET-123"
RUN_ID = "run-777"
STRONG_SECRET = "k3vQ9xT2mZp8Lw4RnB7yHs1D"


# ── Fake Paperclip ─────────────────────────────────────────────────────────────

class FakePaperclip:
    def __init__(self, issues=None, locked=(), fail_me=False):
        self.me = {"id": "agent-1", "name": "GawdBot", "role": "engineer", "title": "Automation Engineer", "companyId": "co-1"}
        self.issues = {i["id"]: dict(i) for i in (issues or [])}
        self.locked = set(locked)                       # ids another agent holds: checkout returns 409
        self.fail_me = fail_me
        self.comments: dict[str, list[dict]] = {i: [] for i in self.issues}
        self.created: list[dict] = []
        self.requests: list[dict] = []

    def app(self) -> web.Application:
        @web.middleware
        async def record(request, handler):
            body = await request.json() if request.can_read_body else None
            self.requests.append({"method": request.method, "path": request.path, "query": dict(request.query),
                                  "auth": request.headers.get("Authorization"),
                                  "run_id": request.headers.get("X-Paperclip-Run-Id"), "json": body})
            if request.headers.get("Authorization") != f"Bearer {TOKEN}":
                return web.json_response({"error": "unauthorized"}, status=401)
            return await handler(request)

        a = web.Application(middlewares=[record])
        a.router.add_get("/api/agents/me", self.get_me)
        a.router.add_get("/api/companies/{cid}/issues", self.list_issues)
        a.router.add_post("/api/companies/{cid}/issues", self.create_issue)
        a.router.add_post("/api/issues/{id}/checkout", self.checkout)
        a.router.add_get("/api/issues/{id}", self.get_issue)
        a.router.add_patch("/api/issues/{id}", self.patch_issue)
        a.router.add_get("/api/issues/{id}/comments", self.list_comments)
        a.router.add_post("/api/issues/{id}/comments", self.add_comment)
        return a

    async def get_me(self, r):
        if self.fail_me:
            return web.json_response({"error": "boom"}, status=500)
        return web.json_response(self.me)

    async def list_issues(self, r):
        wanted = set(r.query.get("status", "").split(","))
        mine = [i for i in self.issues.values()
                if i.get("assignee") == r.query.get("assigneeAgentId") and i["status"] in wanted]
        return web.json_response(mine)

    async def checkout(self, r):
        iid = r.match_info["id"]
        if iid in self.locked:
            return web.json_response({"error": "checked out by another agent"}, status=409)
        self.issues[iid]["status"] = "in_progress"
        return web.json_response(self.issues[iid])

    async def get_issue(self, r):
        return web.json_response(self.issues[r.match_info["id"]])

    async def patch_issue(self, r):
        body = await r.json()
        self.issues[r.match_info["id"]].update(status=body["status"])
        return web.json_response(self.issues[r.match_info["id"]])

    async def list_comments(self, r):
        return web.json_response(self.comments[r.match_info["id"]])

    async def add_comment(self, r):
        body = await r.json()
        self.comments[r.match_info["id"]].append({"id": f"c{len(self.comments[r.match_info['id']])}", **body})
        return web.json_response({"ok": True}, status=201)

    async def create_issue(self, r):
        body = await r.json()
        self.created.append(body)
        return web.json_response({"id": f"new-{len(self.created)}", **body}, status=201)

    def paths(self, method=None):
        return [q["path"] for q in self.requests if method is None or q["method"] == method]


async def serve(app: web.Application) -> tuple[web.AppRunner, int]:
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, site._server.sockets[0].getsockname()[1]


def issue(iid, status="todo", **extra):
    return {"id": iid, "title": f"Task {iid}", "status": status, "priority": "high", "assignee": "agent-1", **extra}


def env_for(port, **extra) -> paperclip.HeartbeatEnv:
    return paperclip.HeartbeatEnv(api_url=f"http://127.0.0.1:{port}", api_key=TOKEN, run_id=RUN_ID, **extra)


# ── Fake brain ─────────────────────────────────────────────────────────────────

def call(name, **args):
    return (name, args)


def reply(text="", *calls):
    return {"content": text, "provider": "fake",
            "tool_calls": [{"id": f"t{i}", "name": n, "arguments": json.dumps(a)} for i, (n, a) in enumerate(calls)]}


@pytest.fixture
def brain(monkeypatch):
    seen: list[list[dict]] = []
    script: list[dict] = []

    async def chat(messages, tools=None, system="", **kw):
        seen.append(json.loads(json.dumps(messages)))
        return script.pop(0)

    async def no_store(*a, **k):
        return 1

    monkeypatch.setattr(agent.llm, "chat", chat)
    monkeypatch.setattr(memory, "store", no_store)

    def load(*replies):
        script.extend(replies)
        return seen
    return load


def heartbeat(fake: FakePaperclip, ask=None, **env_extra) -> paperclip.HeartbeatResult:
    async def go():
        runner, port = await serve(fake.app())
        try:
            return await paperclip.run_heartbeat(env_for(port, **env_extra), ask=ask)
        finally:
            await runner.cleanup()
    return asyncio.run(go())


# ── The heartbeat procedure ────────────────────────────────────────────────────

def test_full_heartbeat_checks_out_works_and_finishes(brain):
    fake = FakePaperclip([issue("i1", goal={"id": "g1", "title": "Launch the single"},
                                ancestors=[{"title": "Q4 rollout"}], description="Draft the press note.")])
    seen = brain(
        reply("starting", call("paperclip_comment", body="Halfway there")),
        reply("", call("paperclip_finish", status="done", comment="Drafted and saved the note.")),
        reply("All done."),
    )
    result = heartbeat(fake)

    assert result.outcome == "finished" and result.issue_id == "i1"
    assert fake.issues["i1"]["status"] == "done"
    checkout = next(q for q in fake.requests if q["path"].endswith("/checkout"))
    assert checkout["json"]["agentId"] == "agent-1" and "in_progress" in checkout["json"]["expectedStatuses"]
    patch = next(q for q in fake.requests if q["method"] == "PATCH")
    assert patch["json"] == {"status": "done", "comment": "Drafted and saved the note."}
    assert all(q["auth"] == f"Bearer {TOKEN}" and q["run_id"] == RUN_ID for q in fake.requests)
    assert fake.comments["i1"][0]["body"] == "Halfway there"

    brief = seen[0][0]["content"]
    assert "Launch the single" in brief and "Q4 rollout" in brief and "Draft the press note." in brief
    assert "Automation Engineer" in brief


def test_woken_task_is_worked_first(brain):
    fake = FakePaperclip([issue("i1"), issue("i2")])
    brain(reply("", call("paperclip_finish", status="done", comment="ok")), reply("done"))
    result = heartbeat(fake, task_id="i2")
    assert result.issue_id == "i2"
    assert fake.paths("POST") == ["/api/issues/i2/checkout"]


def test_order_follows_the_protocol():
    env = paperclip.HeartbeatEnv(api_url="http://localhost", api_key="x")
    inbox = [issue("t1", "todo"), issue("b1", "blocked"), issue("r1", "in_review"), issue("p1", "in_progress"), issue("t2", "todo")]
    assert [i["id"] for i in paperclip.order_candidates(inbox, env)] == ["p1", "t1", "t2"]
    env.wake_comment_id = "c9"
    assert [i["id"] for i in paperclip.order_candidates(inbox, env)] == ["p1", "r1", "t1", "t2"]


def test_conflict_is_never_retried_and_next_task_is_taken(brain):
    fake = FakePaperclip([issue("i1"), issue("i2")], locked={"i1"})
    brain(reply("", call("paperclip_finish", status="done", comment="ok")), reply("done"))
    result = heartbeat(fake)
    assert result.issue_id == "i2"
    checkouts = [p for p in fake.paths("POST") if p.endswith("/checkout")]
    assert checkouts == ["/api/issues/i1/checkout", "/api/issues/i2/checkout"]   # i1 tried exactly once


def test_all_tasks_taken_means_no_work_and_no_brain(brain):
    seen = brain()
    fake = FakePaperclip([issue("i1")], locked={"i1"})
    result = heartbeat(fake)
    assert result.outcome == "conflict" and result.ok and seen == []


def test_empty_inbox_is_idle(brain):
    seen = brain()
    fake = FakePaperclip([issue("x", status="done")])
    result = heartbeat(fake)
    assert result.outcome == "idle" and seen == []
    assert not any(p.endswith("/checkout") for p in fake.paths())


def test_agent_that_forgets_to_finish_still_leaves_a_note(brain):
    fake = FakePaperclip([issue("i1")])
    brain(reply("I looked into it and need the API key from Sun."))
    result = heartbeat(fake)
    assert result.outcome == "progress"
    assert "need the API key from Sun" in fake.comments["i1"][0]["body"]
    assert fake.paths("PATCH") == [] and fake.issues["i1"]["status"] == "in_progress"


def test_paperclip_errors_become_results_not_crashes(brain):
    result = heartbeat(FakePaperclip(fail_me=True))
    assert result.outcome == "error" and "500" in result.message and not result.ok


# ── Security ───────────────────────────────────────────────────────────────────

class ExplodingSession:
    """Any network use fails the test."""
    def request(self, *a, **k):
        raise AssertionError("network was used")


def test_token_is_never_sent_to_a_host_outside_the_allowlist():
    env = paperclip.HeartbeatEnv(api_url="https://evil.example.com", api_key=TOKEN)
    result = asyncio.run(paperclip.run_heartbeat(env, session=ExplodingSession()))
    assert result.outcome == "error" and "PAPERCLIP_ALLOWED_HOSTS" in result.message


@pytest.mark.parametrize("url", ["", "ftp://localhost", "localhost:3100", "http://"])
def test_bad_urls_are_refused(url):
    assert paperclip.HeartbeatEnv(api_url=url, api_key=TOKEN).problem() is not None


def test_missing_token_is_refused():
    assert "API_KEY" in paperclip.HeartbeatEnv(api_url="http://localhost:3100", api_key="").problem()


def test_token_never_lands_in_the_event_log_or_repr(brain):
    fake = FakePaperclip([issue("i1")])
    brain(reply("", call("paperclip_finish", status="done", comment="ok")), reply("done"))
    result = heartbeat(fake)
    rows = events.session_events(result.session_id)
    assert rows and TOKEN not in json.dumps(rows)
    assert TOKEN not in repr(env_for(1))


def test_subtask_needs_approval_and_is_blocked_without_it(brain):
    fake = FakePaperclip([issue("i1")])
    seen = brain(reply("", call("paperclip_create_subtask", title="Mix", description="Mix the track")), reply("could not delegate"))
    heartbeat(fake)
    assert fake.created == []
    tool_msgs = [m["content"] for m in seen[-1] if m["role"] == "tool"]
    assert tool_msgs[0].startswith("NOT RUN: paperclip_create_subtask")


def test_subtask_is_created_under_the_task_when_approved(brain):
    async def approve(req):
        approvals.resolve(req.id, True, by="sun", channel="paperclip")

    fake = FakePaperclip([issue("i1", goalId="g1", projectId="p1")])
    brain(reply("", call("paperclip_create_subtask", title="Mix", description="Mix it", assignee_agent_id="mixer")),
          reply("", call("paperclip_finish", status="in_review", comment="Delegated mixing.")), reply("ok"))
    heartbeat(fake, ask=approve)
    assert fake.created == [{"title": "Mix", "description": "Mix it", "status": "todo",
                             "assigneeAgentId": "mixer", "parentId": "i1", "goalId": "g1", "projectId": "p1"}]


def test_paperclip_tools_do_nothing_outside_a_heartbeat():
    assert asyncio.run(registry.call("paperclip_comment", {"body": "hi"})) == paperclip_tools_msg()
    assert asyncio.run(registry.call("paperclip_finish", {"status": "done", "comment": "x"})) == paperclip_tools_msg()


def paperclip_tools_msg():
    from tools import paperclip_tools
    return paperclip_tools.NOT_IN_TASK


def test_tool_risk_levels():
    assert registry.get("paperclip_comment").risk_for({}) == registry.SAFE
    assert registry.get("paperclip_finish").risk_for({}) == registry.SAFE
    assert registry.get("paperclip_create_subtask").risk_for({}) == registry.ASK


# ── Daemon endpoint and the heartbeat command ──────────────────────────────────

@pytest.fixture
def daemon_config(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", STRONG_SECRET)
    monkeypatch.setattr(config, "WEBHOOK_ENABLED", True)
    monkeypatch.setattr(config, "PAPERCLIP_ENABLED", True)
    calls = []

    async def fake_run_heartbeat(env, ask=None, session=None):
        calls.append(env)
        return paperclip.HeartbeatResult("finished", "Reported done.", "i1", "sess-1")

    monkeypatch.setattr(paperclip, "run_heartbeat", fake_run_heartbeat)
    return calls


def _post(port, secret, payload):
    async def go():
        async with ClientSession() as s:
            async with s.post(f"http://127.0.0.1:{port}/paperclip/heartbeat", json=payload,
                              headers={"X-Webhook-Secret": secret}) as r:
                return r.status, await r.json()
    return go()


def test_endpoint_is_off_unless_enabled(daemon_config, monkeypatch):
    monkeypatch.setattr(config, "PAPERCLIP_ENABLED", False)

    async def go():
        runner, port = await serve(webhook_server.build_app())
        try:
            return await _post(port, STRONG_SECRET, {"env": {}})
        finally:
            await runner.cleanup()
    status, _ = asyncio.run(go())
    assert status == 404 and daemon_config == []


def test_endpoint_requires_the_secret(daemon_config):
    async def go():
        runner, port = await serve(webhook_server.build_app())
        try:
            return await _post(port, "wrong-secret-wrong-secret", {"env": {}})
        finally:
            await runner.cleanup()
    status, _ = asyncio.run(go())
    assert status == 401 and daemon_config == []


def test_command_hands_the_heartbeat_to_the_running_gawdbot(daemon_config, monkeypatch, capsys):
    async def go():
        runner, port = await serve(webhook_server.build_app())
        monkeypatch.setattr(config, "WEBHOOK_PORT", port)
        try:
            return await hb_cli.run({"PAPERCLIP_API_URL": "http://localhost:3100", "PAPERCLIP_API_KEY": TOKEN,
                                     "PAPERCLIP_RUN_ID": RUN_ID, "PAPERCLIP_TASK_ID": "i1"})
        finally:
            await runner.cleanup()

    assert asyncio.run(go()) == 0
    assert len(daemon_config) == 1 and daemon_config[0].task_id == "i1" and daemon_config[0].api_key == TOKEN
    out = capsys.readouterr().out
    assert "handing the heartbeat to the running GawdBot" in out and TOKEN not in out


def test_command_runs_standalone_when_gawdbot_is_down(daemon_config, monkeypatch, capsys):
    with socket.socket() as s:                    # find a port nothing is listening on
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    monkeypatch.setattr(config, "WEBHOOK_PORT", free_port)
    code = asyncio.run(hb_cli.run({"PAPERCLIP_API_URL": "http://localhost:3100", "PAPERCLIP_API_KEY": TOKEN}))
    assert code == 0 and len(daemon_config) == 1
    out = capsys.readouterr().out
    assert "not running" in out and "running standalone" in out and TOKEN not in out


def test_command_without_paperclip_env_is_a_setup_error(capsys):
    assert asyncio.run(hb_cli.run({})) == 2
    assert "PAPERCLIP_API_URL" in capsys.readouterr().out
