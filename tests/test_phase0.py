"""
Phase 0 security and reliability tests.

Run from the project root:  python -m pytest tests -v
Each test is named for the thing an attacker (or a bug) would try.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from core import llm, security
from tools import code_tools


# ── Protected paths ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", [".env", ".env.local", ".env.production", "server.pem", "id_rsa", "id_ed25519.pub", "api.key", ".netrc"])
def test_secret_file_names_are_protected(tmp_path, name):
    assert security.protected_reason(tmp_path / name) is not None


def test_env_example_is_readable(tmp_path):
    assert security.protected_reason(tmp_path / ".env.example") is None


def test_normal_files_are_readable(tmp_path):
    assert security.protected_reason(tmp_path / "notes.md") is None
    assert security.protected_reason(tmp_path / "environment.py") is None


def test_symlink_to_env_is_caught(tmp_path):
    real = tmp_path / ".env"
    real.write_text("API_KEY=abc")
    link = tmp_path / "harmless.txt"
    link.symlink_to(real)
    assert security.protected_reason(link) is not None


def test_credential_folders_and_git_are_protected(tmp_path):
    assert security.protected_reason(tmp_path / ".ssh" / "config") is not None
    assert security.protected_reason(tmp_path / ".aws" / "anything") is not None
    assert security.protected_reason(tmp_path / ".git" / "config") is not None


def test_internal_data_folder_is_protected():
    assert security.protected_reason(config.PROJECT_ROOT / "data" / "memory.db") is not None
    assert security.protected_reason(config.PROJECT_ROOT / "backups" / "x.tar.gz") is not None


# ── File tools ─────────────────────────────────────────────────────────────────

def test_read_file_refuses_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=sk-real-secret")
    result = code_tools.read_file(str(env))
    assert result.startswith("Refused")
    assert "sk-real-secret" not in result


def test_read_file_reads_normal_files(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("hi there")
    assert code_tools.read_file(str(f)) == "hi there"


def test_write_file_refuses_env(tmp_path):
    target = tmp_path / ".env"
    assert code_tools.write_file(str(target), "X=1").startswith("Refused")
    assert not target.exists()


def test_write_file_works_normally(tmp_path):
    f = tmp_path / "out.txt"
    assert code_tools.write_file(str(f), "ok").startswith("Written")
    assert f.read_text() == "ok"


# ── Shell tool ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cmd", [
    "cat .env", "cat ./.env", "type .env", "cat .env.local", "less /home/me/.ssh/id_rsa",
    "printenv", "env", "env | grep KEY", "cat /proc/self/environ", "sqlite3 data/memory.db .dump",
    "cat ~/.aws/credentials",
])
def test_secret_hunting_commands_are_blocked(cmd):
    assert security.command_touches_secrets(cmd) is not None, cmd


@pytest.mark.parametrize("cmd", [
    "ls -la", "git status", "python main.py doctor", "cat .env.example", "env python script.py",
    "echo environment", "pip install python-dotenv", "cat my.environment.txt",
])
def test_ordinary_commands_are_allowed(cmd):
    assert security.command_touches_secrets(cmd) is None, cmd


def test_run_command_refuses_secret_hunting():
    out = asyncio.run(code_tools.run_command("cat .env"))
    assert out.startswith("Refused")


def test_child_processes_do_not_inherit_secrets(monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY", "super-secret-value")
    monkeypatch.setenv("HARMLESS_SETTING", "visible")
    py = f'"{sys.executable}" -c "import os; print(os.environ.get(\'FAKE_API_KEY\'), os.environ.get(\'HARMLESS_SETTING\'))"'
    out = asyncio.run(code_tools.run_command(py))
    assert "super-secret-value" not in out
    assert "None visible" in out


def test_scrubbed_env_removes_all_secret_like_names(monkeypatch):
    for name in ("MY_TOKEN", "DB_PASSWORD", "THING_SECRET", "SOME_API_KEY", "AWS_CREDENTIAL_X"):
        monkeypatch.setenv(name, "x")
    env = security.scrubbed_env()
    for name in ("MY_TOKEN", "DB_PASSWORD", "THING_SECRET", "SOME_API_KEY", "AWS_CREDENTIAL_X"):
        assert name not in env
    assert "PATH" in env


# ── Allowlists ─────────────────────────────────────────────────────────────────

def test_empty_allowlist_lets_nobody_in():
    assert security.is_authorized(123, set()) is False
    assert security.is_authorized(123, []) is False
    assert security.is_authorized(123, [""]) is False


def test_allowlist_matches_ints_and_strings():
    assert security.is_authorized(123, {"123"}) is True
    assert security.is_authorized("123", [123]) is True
    assert security.is_authorized(456, {"123"}) is False


def test_id_set_parsing_fails_closed(monkeypatch):
    monkeypatch.setenv("T_IDS", "0")
    assert config._id_set("T_IDS") == set()           # old "0 = allow everyone" now means nobody
    monkeypatch.setenv("T_IDS", "1, 2;3 ,,")
    assert config._id_set("T_IDS") == {"1", "2", "3"}
    monkeypatch.delenv("T_IDS")
    assert config._id_set("T_IDS") == set()


# ── Webhook secret ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("secret", ["", "changeme", "CHANGEME", "password", "short", "123456789012345"])
def test_weak_secrets_are_rejected(secret):
    assert security.secret_is_weak(secret) is True


def test_strong_secret_is_accepted():
    assert security.secret_is_weak("k3vQ9xT2mZp8Lw4RnB7yHs1D") is False


def test_constant_time_equal():
    assert security.constant_time_equal("abc", "abc") is True
    assert security.constant_time_equal("abc", "abd") is False
    assert security.constant_time_equal("abc", "") is False


def test_webhook_default_binds_to_localhost_only():
    assert config.WEBHOOK_HOST in ("127.0.0.1", "localhost", "::1") or os.getenv("WEBHOOK_HOST")


def test_webhook_refuses_to_start_with_default_secret(monkeypatch, caplog):
    from interfaces import webhook_server
    monkeypatch.setattr(config, "WEBHOOK_ENABLED", True)
    monkeypatch.setattr(config, "WEBHOOK_SECRET", "changeme")
    with caplog.at_level(logging.ERROR):
        asyncio.run(asyncio.wait_for(webhook_server.run(), timeout=3))   # returns instead of serving forever
    assert "NOT started" in caplog.text


# ── Chat channels fail closed ──────────────────────────────────────────────────

def test_telegram_refuses_to_start_without_allowlist(monkeypatch, caplog):
    from interfaces import telegram_bot
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123:fake")
    monkeypatch.setattr(config, "TELEGRAM_ALLOWED_USERS", set())
    with caplog.at_level(logging.ERROR):
        asyncio.run(asyncio.wait_for(telegram_bot.run(), timeout=3))
    assert "NOT started" in caplog.text


def test_discord_refuses_to_start_without_allowlist(monkeypatch, caplog):
    from interfaces import discord_bot
    monkeypatch.setattr(config, "DISCORD_ENABLED", True)
    monkeypatch.setattr(config, "DISCORD_BOT_TOKEN", "fake")
    monkeypatch.setattr(config, "DISCORD_ALLOWED_USERS", set())
    with caplog.at_level(logging.ERROR):
        asyncio.run(asyncio.wait_for(discord_bot.run(), timeout=3))
    assert "NOT started" in caplog.text


def test_slack_refuses_to_start_without_allowlist(monkeypatch, caplog):
    from interfaces import slack_bot
    monkeypatch.setattr(config, "SLACK_ENABLED", True)
    monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "xoxb-fake")
    monkeypatch.setattr(config, "SLACK_APP_TOKEN", "xapp-fake")
    monkeypatch.setattr(config, "SLACK_ALLOWED_USERS", set())
    with caplog.at_level(logging.ERROR):
        asyncio.run(asyncio.wait_for(slack_bot.run(), timeout=3))
    assert "NOT started" in caplog.text


# ── Anthropic translation ──────────────────────────────────────────────────────

def _conversation():
    return [
        {"role": "user", "content": "list my files and check git"},
        {"role": "assistant", "content": "On it.", "tool_calls": [
            {"id": "t1", "type": "function", "function": {"name": "run_command", "arguments": json.dumps({"command": "ls"})}},
            {"id": "t2", "type": "function", "function": {"name": "git_status", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "t1", "content": "a.py b.py"},
        {"role": "tool", "tool_call_id": "t2", "content": "clean"},
    ]


def test_anthropic_translation_shapes_tool_calls_correctly():
    out = llm._to_anthropic_messages(_conversation())
    assert [m["role"] for m in out] == ["user", "assistant", "user"]

    assistant = out[1]["content"]
    assert assistant[0] == {"type": "text", "text": "On it."}
    assert assistant[1] == {"type": "tool_use", "id": "t1", "name": "run_command", "input": {"command": "ls"}}
    assert assistant[2]["input"] == {}

    # Both tool results must ride together in ONE user message, as the API requires.
    results = out[2]["content"]
    assert [r["type"] for r in results] == ["tool_result", "tool_result"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]


def test_anthropic_translation_skips_system_and_empty_messages():
    msgs = [
        {"role": "system", "content": "ignored here, sent separately"},
        {"role": "user", "content": ""},
        {"role": "user", "content": "real question"},
    ]
    out = llm._to_anthropic_messages(msgs)
    assert out == [{"role": "user", "content": [{"type": "text", "text": "real question"}]}]


def test_anthropic_translation_survives_corrupt_arguments():
    msgs = [{"role": "assistant", "content": None, "tool_calls": [
        {"id": "x", "type": "function", "function": {"name": "t", "arguments": "{not json"}}]}]
    out = llm._to_anthropic_messages(msgs)
    assert out[0]["content"][0]["input"] == {}


def test_anthropic_response_arguments_are_real_json(monkeypatch):
    block_text = SimpleNamespace(type="text", text="Checking.")
    block_tool = SimpleNamespace(type="tool_use", id="abc", name="web_search", input={"query": "it's \"quoted\"", "max_results": 3})
    captured = {}

    class FakeMessages:
        async def create(self, **params):
            captured.update(params)
            return SimpleNamespace(content=[block_text, block_tool], stop_reason="tool_use")

    monkeypatch.setattr(llm, "_anthropic_client", lambda *a, **k: SimpleNamespace(messages=FakeMessages()))
    result = asyncio.run(llm._call_anthropic(_conversation(), tools=[{"type": "function", "function": {"name": "git_status", "parameters": {"type": "object", "properties": {}}}}], system="sys"))

    args = json.loads(result["tool_calls"][0]["arguments"])        # the old code made this raise
    assert args == {"query": "it's \"quoted\"", "max_results": 3}
    assert result["content"] == "Checking."
    assert captured["system"] == "sys"
    assert captured["messages"][0]["content"][0]["type"] == "text"  # translated, not raw OpenAI shape


# ── Agent loop ─────────────────────────────────────────────────────────────────

def test_agent_reports_bad_arguments_instead_of_running_tool(monkeypatch):
    from core import agent, memory

    dispatched = []

    async def fake_dispatch(name, args):
        dispatched.append((name, args))
        return "should not happen"

    async def fake_store(*a, **k):
        return 1

    seen_messages = []
    replies = iter([
        {"content": "", "tool_calls": [{"id": "c1", "name": "run_command", "arguments": "{broken json"}], "finish_reason": "tool_calls", "provider": "fake"},
        {"content": "done", "tool_calls": [], "finish_reason": "stop", "provider": "fake"},
    ])

    async def fake_chat(messages, tools=None, system="", **kw):
        seen_messages.append(json.loads(json.dumps(messages)))
        return next(replies)

    monkeypatch.setattr(agent, "_dispatch", fake_dispatch)
    monkeypatch.setattr(agent.llm, "chat", fake_chat)
    monkeypatch.setattr(memory, "store", fake_store)

    assert asyncio.run(agent.run("do a thing")) == "done"
    assert dispatched == []                                          # the tool never ran with empty arguments
    tool_msg = [m for m in seen_messages[-1] if m["role"] == "tool"][0]
    assert "not valid JSON" in tool_msg["content"]
