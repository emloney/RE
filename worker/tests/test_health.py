"""Warnings about the bot itself, the CLI's crash path, and the scheduled workflow file."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import yaml

from internradar import __main__ as cli
from internradar.config import repo_root
from internradar.health import (
    crash_warning,
    notify,
    run_warnings,
    should_warn_streak,
)
from internradar.run import RunResult
from internradar.store import RunSummary
from internradar.telegram import TelegramError

from .test_telegram import FakeSender


def result(status="partial", failures=(), streaks=None, checked=5) -> RunResult:
    summary = RunSummary(trigger="schedule", started_at=datetime.now(UTC), status=status)
    summary.companies_checked = checked
    summary.failures = list(failures)
    summary.companies_failed = len(failures)
    return RunResult(summary=summary, failure_streaks=dict(streaks or {}))


@pytest.mark.parametrize(
    ("streak", "warn"), [(1, False), (2, False), (3, True), (4, False), (15, True), (27, True)]
)
def test_streak_warnings_at_3_then_every_12_runs(streak, warn):
    assert should_warn_streak(streak) is warn


def test_company_streak_warning_is_escaped():
    r = result(failures=[("A&B <Co>", "HTTP 500 <html>")], streaks={"A&B <Co>": 3})
    (text,) = run_warnings(r)
    assert "A&amp;B &lt;Co&gt;" in text and "HTTP 500 &lt;html&gt;" in text
    assert "3 runs in a row" in text


def test_no_warning_for_a_first_or_second_failure():
    assert run_warnings(result(failures=[("Acme", "x")], streaks={"Acme": 2})) == []


def test_whole_run_failure_is_one_warning_not_one_per_company():
    failures = [(f"C{i}", "timeout") for i in range(5)]
    r = result(status="failed", failures=failures, streaks={n: 3 for n, _ in failures})
    (text,) = run_warnings(r)
    assert "all 5 companies failed" in text


def test_crash_warning_is_escaped_and_short():
    text = crash_warning(RuntimeError("<boom> " + "x" * 2000))
    assert "&lt;boom&gt;" in text and len(text) < 700


def test_notify_never_raises_and_writes_the_marker(tmp_path, monkeypatch):
    marker = tmp_path / "warned"
    monkeypatch.setenv("INTERNRADAR_WARNED_FILE", str(marker))

    class Broken:
        def send(self, text, buttons=None):
            raise TelegramError("down")

    assert notify(Broken(), ["hi"]) == 0 and not marker.exists()
    assert notify(None, ["hi"]) == 0
    assert notify(FakeSender(), ["hi"]) == 1 and marker.exists()


def test_cli_reports_a_crash_on_telegram_then_reraises(monkeypatch):
    sender = FakeSender()
    monkeypatch.setattr(cli, "_make_sender", lambda dry_run: sender)

    def broken_store(dry_run):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(cli, "_make_store", broken_store)
    with pytest.raises(RuntimeError):
        cli.main(["run"])
    ((text, _),) = sender.sent
    assert "InternRadar run crashed" in text and "database unreachable" in text


def test_step_summary_is_written_on_github_actions(tmp_path, monkeypatch):
    path = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(path))
    cli._write_step_summary(result(failures=[("Acme", "HTTP 500")]))
    text = path.read_text(encoding="utf-8")
    assert "InternRadar run: partial" in text and "Acme: `HTTP 500`" in text


# -- the workflow file --------------------------------------------------------------

WORKFLOW = yaml.safe_load((repo_root() / ".github/workflows/check.yml").read_text("utf-8"))
# PyYAML reads the bare key `on` as True (YAML 1.1); GitHub reads it as "on".
TRIGGERS = WORKFLOW.get("on", WORKFLOW.get(True))


def test_schedule_is_8_12_16_20_ist():
    (entry,) = TRIGGERS["schedule"]
    minute, hours, *rest = entry["cron"].split()
    assert rest == ["*", "*", "*"]
    ist = sorted(((int(h) * 60 + int(minute) + 330) // 60) % 24 for h in hours.split(","))
    ist_minutes = {(int(minute) + 330) % 60}
    assert ist == [8, 12, 16, 20] and ist_minutes == {0}


def test_manual_trigger_and_inputs_exist():
    inputs = TRIGGERS["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"company", "dry_run", "alert_on_baseline"}


def test_workflow_never_interpolates_inputs_into_shell():
    steps = WORKFLOW["jobs"]["check"]["steps"]
    for step in steps:
        assert "${{ inputs." not in step.get("run", ""), step.get("name")


def test_every_secret_the_worker_reads_is_passed():
    env = WORKFLOW["jobs"]["check"]["env"]
    for name in (
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "GROQ_API_KEY",
        "CONTACT_EMAIL",
    ):
        assert env[name] == f"${{{{ secrets.{name} }}}}"
