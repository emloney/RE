"""telegram set-webhook / webhook-info, and Python <-> TypeScript consistency."""

from __future__ import annotations

import re

import pytest

from internradar import __main__ as cli
from internradar.alerts import STATUS_BUTTONS
from internradar.config import repo_root
from internradar.telegram import TelegramClient

TS = (repo_root() / "server/lib/commands.ts").read_text(encoding="utf-8")
TS_BUTTONS = (repo_root() / "server/lib/buttons.ts").read_text(encoding="utf-8")


@pytest.fixture
def calls(monkeypatch):
    made: list[tuple[str, dict]] = []

    def fake_call(self, method, payload):
        made.append((method, payload))
        return {"url": "", "username": "testbot", "first_name": "Test"}

    monkeypatch.setattr(TelegramClient, "call", fake_call)
    monkeypatch.setattr(cli, "load_env", lambda: None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    return made


def test_set_webhook_sends_secret_and_command_menu(calls, monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "abc_DEF-123")
    assert cli.main(["telegram", "set-webhook", "https://x.vercel.app/api/telegram"]) == 0
    methods = dict(calls)
    hook = methods["setWebhook"]
    assert hook["url"] == "https://x.vercel.app/api/telegram"
    assert hook["secret_token"] == "abc_DEF-123"
    assert hook["allowed_updates"] == ["message", "callback_query"]
    commands = [c["command"] for c in methods["setMyCommands"]["commands"]]
    assert "minscore" in commands and "addkw" in commands


@pytest.mark.parametrize("secret", ["", "has space", "bad!chars", "x" * 257])
def test_set_webhook_refuses_bad_secrets(calls, monkeypatch, secret):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", secret)
    assert cli.main(["telegram", "set-webhook", "https://x.vercel.app/api/telegram"]) == 2
    assert "setWebhook" not in dict(calls)


def test_set_webhook_needs_https(calls, monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "ok")
    assert cli.main(["telegram", "set-webhook", "http://x.vercel.app/api/telegram"]) == 2


def test_command_menu_matches_the_webhook():
    ts_commands = re.findall(r'^\s*\["(\w+)", "', TS, flags=re.MULTILINE)
    assert ts_commands == [c for c, _ in cli.BOT_COMMANDS]


def test_button_codes_match_the_webhook():
    ts_codes = re.findall(r'code: "(\w+)", label: "([^"]+)"', TS_BUTTONS)
    assert ts_codes == [(code, label) for label, code in STATUS_BUTTONS]
