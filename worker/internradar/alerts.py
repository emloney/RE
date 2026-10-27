"""Build and send job alerts.

- One message per new matching job, best score first (unscored last).
- More than 10 in one run: the top 10 individually, then one summary of the rest.
- alerted_at is written right after each send, so a re-run never repeats an alert.
- /pause and /minscore (Phase 4b) are read from the settings row.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from internradar.telegram import MAX_TEXT, Button, TelegramError, esc

log = logging.getLogger(__name__)

# India has no daylight saving time, so a fixed offset is exact (UTC+5:30).
IST = timezone(timedelta(hours=5, minutes=30), "IST")
MAX_INDIVIDUAL = 10
TITLE_CHARS = 200
WHY_CHARS = 300

# Button presses come back to the webhook (Phase 4b) as "<code>:<job id>".
STATUS_BUTTONS: list[tuple[str, str]] = [
    ("✅ Applied", "ap"),
    ("⭐ Interested", "in"),
    ("❌ Skip", "sk"),
]


@dataclass(frozen=True)
class Alert:
    """A matched job waiting to be alerted (read from the jobs table)."""

    job_id: str
    company: str
    title: str
    location: str
    url: str
    first_seen_at: datetime
    score: int | None = None
    why: str | None = None
    missing_skills: list[str] = field(default_factory=list)


class Sender(Protocol):
    def send(self, text: str, buttons: list[Button] | None = None) -> int: ...


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_alert(alert: Alert) -> str:
    seen = alert.first_seen_at.astimezone(IST).strftime("%d %b, %I:%M %p IST")
    if alert.score is not None:
        match = f"{alert.score}/10"
        if alert.why:
            match += f" — {esc(_cut(alert.why, WHY_CHARS))}"
    else:
        match = "not scored"
    lines = [
        f"🆕 <b>{esc(_cut(alert.company, 100))} — {esc(_cut(alert.title, TITLE_CHARS))}</b>",
        f"📍 {esc(_cut(alert.location or 'Location not stated', 200))}   🕒 seen {seen}",
        f"⭐ Match: {match}",
    ]
    if alert.missing_skills:
        lines.append(f"🧩 Missing: {esc(_cut(', '.join(alert.missing_skills[:5]), 200))}")
    lines.append(f"🔗 Apply: {esc(alert.url)}")
    text = "\n".join(lines)
    if len(text) > MAX_TEXT:  # only possible with an absurd URL
        text = text[: MAX_TEXT - 1] + "…"
    return text


def alert_buttons(job_id: str) -> list[Button]:
    return [(label, f"{code}:{job_id}") for label, code in STATUS_BUTTONS]


def format_overflow(alerts: list[Alert]) -> list[tuple[str, list[Alert]]]:
    """The jobs beyond the top 10, in as few messages as fit under the length limit.
    Returns (message, the alerts it lists) pairs."""
    messages: list[tuple[str, list[Alert]]] = []
    current, listed = f"➕ <b>{len(alerts)} more new matches</b>", []
    for a in alerts:
        score = f"{a.score}/10" if a.score is not None else "–"
        line = f"• {score} {esc(_cut(a.company, 60))} — {esc(_cut(a.title, 120))}\n  {esc(a.url)}"
        if listed and len(current) + 1 + len(line) > MAX_TEXT:
            messages.append((current, listed))
            current, listed = "➕ <b>(continued)</b>", []
        current += "\n" + line
        listed.append(a)
    messages.append((current, listed))
    return messages


def sort_alerts(alerts: list[Alert]) -> list[Alert]:
    return sorted(
        alerts,
        key=lambda a: (a.score is None, -(a.score or 0), -a.first_seen_at.timestamp()),
    )


class AlertStore(Protocol):
    def alert_candidates(self, since: datetime) -> list[Alert]: ...

    def mark_alerted(self, job_ids: list[str], now: datetime) -> None: ...

    def alert_settings(self) -> dict[str, Any]: ...


@dataclass
class AlertReport:
    sent: int = 0
    held_by_min_score: int = 0
    paused: bool = False
    errors: list[str] = field(default_factory=list)


def send_alerts(
    store: AlertStore,
    sender: Sender,
    since: datetime,
    now: datetime,
    extra: list[Alert] | None = None,
) -> AlertReport:
    """Send every matched job not alerted yet. `extra` adds this run's matches,
    which a --dry-run never saved to the database."""
    report = AlertReport()
    settings = store.alert_settings()
    if settings.get("paused"):
        report.paused = True
        log.info("alerts are paused (/resume to turn them back on)")
        return report

    candidates = store.alert_candidates(since)
    known = {a.job_id for a in candidates}
    candidates += [a for a in extra or [] if a.job_id not in known]
    min_score = settings.get("min_score")
    if min_score is not None:
        # Unscored jobs always go through: never drop a job because the LLM failed.
        kept = [a for a in candidates if a.score is None or a.score >= min_score]
        report.held_by_min_score = len(candidates) - len(kept)
        candidates = kept

    ordered = sort_alerts(candidates)
    for alert in ordered[:MAX_INDIVIDUAL]:
        try:
            sender.send(format_alert(alert), alert_buttons(alert.job_id))
        except TelegramError as exc:
            report.errors.append(f"{alert.company} — {alert.title}: {exc}")
            log.warning("could not send alert for %s: %s", alert.title, exc)
            continue
        store.mark_alerted([alert.job_id], now)  # right away: a crash can't cause a repeat
        report.sent += 1

    rest = ordered[MAX_INDIVIDUAL:]
    if rest:
        for text, chunk in format_overflow(rest):
            try:
                sender.send(text)
            except TelegramError as exc:
                report.errors.append(f"summary of {len(chunk)} jobs: {exc}")
                continue
            store.mark_alerted([a.job_id for a in chunk], now)
            report.sent += len(chunk)
    return report


class PrintSender:
    """--dry-run: print the messages instead of sending them."""

    def send(self, text: str, buttons: list[Button] | None = None) -> int:
        row = "   ".join(f"[{label}]" for label, _ in buttons or [])
        print(f"──── Telegram message ────\n{text}" + (f"\n{row}" if row else "") + "\n")
        return 0
