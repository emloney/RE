"""Telegram warnings about the bot itself: a failed run, a crash, or a company
that keeps failing. Messages are short, HTML-escaped and never contain secrets.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from internradar.alerts import Sender
from internradar.run import RunResult
from internradar.telegram import TelegramError, esc

log = logging.getLogger(__name__)

STREAK_WARN_AT = 3
STREAK_REPEAT_EVERY = 12  # after the first warning, remind every ~3 days (4 runs/day)
# The workflow checks this file so it doesn't send a second, vaguer warning.
WARNED_FILE_ENV = "INTERNRADAR_WARNED_FILE"


def should_warn_streak(streak: int) -> bool:
    if streak < STREAK_WARN_AT:
        return False
    return (streak - STREAK_WARN_AT) % STREAK_REPEAT_EVERY == 0


def run_warnings(result: RunResult) -> list[str]:
    summary = result.summary
    errors = dict(summary.failures)
    warnings = []
    if summary.status == "failed":
        lines = [
            f"⚠️ <b>InternRadar run failed</b>: all {summary.companies_checked} companies failed."
        ]
        lines += [f"• {esc(name)}: {esc(err[:200])}" for name, err in summary.failures[:5]]
        warnings.append("\n".join(lines))
    for name, streak in result.failure_streaks.items():
        if should_warn_streak(streak) and summary.status != "failed":
            warnings.append(
                f"⚠️ <b>{esc(name)}</b> has failed {streak} runs in a row.\n"
                f"Last error: {esc(errors.get(name, 'unknown')[:300])}\n"
                "Its existing jobs are untouched. Check its entry in companies.yaml, "
                "or disable it."
            )
    return warnings


def crash_warning(exc: BaseException) -> str:
    detail = f"{type(exc).__name__}: {exc}"
    return f"❌ <b>InternRadar run crashed</b>\n{esc(detail[:500])}"


def notify(sender: Sender | None, warnings: list[str]) -> int:
    """Send the warnings. Never raises: a broken Telegram shouldn't hide the real error."""
    sent = 0
    for text in warnings:
        if sender is None:
            log.warning("warning (Telegram off): %s", text)
            continue
        try:
            sender.send(text)
            sent += 1
        except TelegramError as exc:
            log.warning("could not send warning: %s", exc)
    marker = os.environ.get(WARNED_FILE_ENV)
    if sent and marker:
        Path(marker).write_text("sent\n", encoding="utf-8")
    return sent
