"""Telegram client and alert messages: HTML escaping, limits, rate limits, ordering."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from internradar.alerts import (
    MAX_INDIVIDUAL,
    Alert,
    alert_buttons,
    format_alert,
    format_overflow,
    send_alerts,
    sort_alerts,
)
from internradar.telegram import MAX_TEXT, TelegramClient, TelegramError, esc

TOKEN = "123456:SECRET-TOKEN"
SEEN = datetime(2026, 10, 27, 2, 30, tzinfo=UTC)  # 08:00 IST


def alert(i: int = 1, score: int | None = 8, **kw) -> Alert:
    base = dict(
        job_id=f"{i:032x}",
        company="Acme",
        title=f"Data Analyst Intern {i}",
        location="Bengaluru",
        url=f"https://jobs.lever.co/acme/{i}",
        first_seen_at=SEEN,
        score=score,
        why="Strong SQL",
    )
    return Alert(**{**base, **kw})


# -- escaping -----------------------------------------------------------------


def test_esc_replaces_the_three_special_characters():
    assert esc('<b>Tom & "Jerry"</b>') == '&lt;b&gt;Tom &amp; "Jerry"&lt;/b&gt;'


def test_alert_escapes_every_piece_of_job_text():
    a = alert(
        company="AT&T <Labs>",
        title="<script>alert(1)</script> ML Intern",
        location="Bengaluru <HQ>",
        url="https://x.example/jobs?id=1&src=<x>",
        why="Knows <Python> & SQL",
        missing_skills=["C++ & <Rust>"],
    )
    text = format_alert(a)
    assert "<script>" not in text and "<Labs>" not in text and "<HQ>" not in text
    assert "AT&amp;T &lt;Labs&gt;" in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "id=1&amp;src=&lt;x&gt;" in text
    assert "Knows &lt;Python&gt; &amp; SQL" in text
    assert "C++ &amp; &lt;Rust&gt;" in text
    # The only real tags are the ones we add.
    stripped = text.replace("<b>", "").replace("</b>", "")
    assert "<" not in stripped and ">" not in stripped


def test_alert_format_matches_the_spec():
    text = format_alert(alert(missing_skills=["Docker"]))
    assert text.splitlines() == [
        "🆕 <b>Acme — Data Analyst Intern 1</b>",
        "📍 Bengaluru   🕒 seen 27 Oct, 08:00 AM IST",
        "⭐ Match: 8/10 — Strong SQL",
        "🧩 Missing: Docker",
        "🔗 Apply: https://jobs.lever.co/acme/1",
    ]


def test_unscored_alert_says_so():
    assert "⭐ Match: not scored" in format_alert(alert(score=None, why=None))


def test_huge_fields_stay_under_the_limit():
    text = format_alert(alert(title="A" * 10_000, why="B" * 10_000, location="C" * 10_000))
    assert len(text) <= MAX_TEXT


def test_button_data_fits_telegrams_64_byte_limit():
    buttons = alert_buttons("f" * 32)
    assert [label for label, _ in buttons] == ["✅ Applied", "⭐ Interested", "❌ Skip"]
    assert all(len(data.encode()) <= 64 for _, data in buttons)
    assert buttons[0][1] == "ap:" + "f" * 32


# -- ordering and overflow --------------------------------------------------------


def test_sort_by_score_with_unscored_last():
    ordered = sort_alerts([alert(1, None), alert(2, 5), alert(3, 9), alert(4, 7)])
    assert [a.score for a in ordered] == [9, 7, 5, None]


def test_overflow_summary_splits_under_the_limit_and_lists_each_job_once():
    many = [alert(i, title="T" * 150) for i in range(300)]
    messages = format_overflow(many)
    assert len(messages) > 1
    assert all(len(text) <= MAX_TEXT for text, _ in messages)
    listed = [a.job_id for _, chunk in messages for a in chunk]
    assert listed == [a.job_id for a in many]
    assert messages[0][0].startswith("➕ <b>300 more new matches</b>")


# -- sending -------------------------------------------------------------------


class FakeSender:
    def __init__(self, fail_titles: set[str] | None = None):
        self.sent: list[tuple[str, list | None]] = []
        self.fail_titles = fail_titles or set()

    def send(self, text, buttons=None):
        if any(t in text for t in self.fail_titles):
            raise TelegramError("400 Bad Request")
        self.sent.append((text, buttons))
        return len(self.sent)


class FakeStore:
    def __init__(self, alerts, settings=None):
        self.alerts = {a.job_id: a for a in alerts}
        self.alerted: dict[str, datetime] = {}
        self.settings = settings or {"paused": False, "min_score": None}

    def alert_candidates(self, since):
        return [a for jid, a in self.alerts.items() if jid not in self.alerted]

    def mark_alerted(self, job_ids, now):
        for jid in job_ids:
            self.alerted[jid] = now

    def alert_settings(self):
        return self.settings


def test_top_ten_individually_then_one_summary():
    store = FakeStore([alert(i, score=i % 10 + 1) for i in range(13)])
    sender = FakeSender()
    report = send_alerts(store, sender, SEEN - timedelta(days=3), SEEN)

    assert report.sent == 13 and len(store.alerted) == 13
    individual = [s for s in sender.sent if s[1]]
    summaries = [s for s in sender.sent if not s[1]]
    assert len(individual) == MAX_INDIVIDUAL and len(summaries) == 1
    assert "10/10" in individual[0][0]  # best first
    assert summaries[0][0].startswith("➕ <b>3 more new matches</b>")


def test_sending_twice_never_repeats_an_alert():
    store = FakeStore([alert(1), alert(2)])
    sender = FakeSender()
    send_alerts(store, sender, SEEN, SEEN)
    send_alerts(store, sender, SEEN, SEEN)
    assert len(sender.sent) == 2


def test_a_failed_send_is_retried_next_run():
    store = FakeStore([alert(1), alert(2)])
    report = send_alerts(store, FakeSender(fail_titles={"Intern 1"}), SEEN, SEEN)
    assert report.sent == 1 and len(report.errors) == 1
    assert list(store.alerted) == [alert(2).job_id]
    send_alerts(store, FakeSender(), SEEN, SEEN)
    assert len(store.alerted) == 2


def test_paused_sends_nothing_and_keeps_jobs_for_later():
    store = FakeStore([alert(1)], {"paused": True, "min_score": None})
    report = send_alerts(store, FakeSender(), SEEN, SEEN)
    assert report.paused and report.sent == 0 and store.alerted == {}


def test_min_score_holds_low_scores_but_never_unscored_jobs():
    store = FakeStore([alert(1, 9), alert(2, 4), alert(3, None)], {"paused": False, "min_score": 7})
    sender = FakeSender()
    report = send_alerts(store, sender, SEEN, SEEN)
    assert report.sent == 2 and report.held_by_min_score == 1
    assert alert(2).job_id not in store.alerted


def test_extra_alerts_are_sent_but_not_duplicated():
    store = FakeStore([alert(1)])
    sender = FakeSender()
    send_alerts(store, sender, SEEN, SEEN, extra=[alert(1), alert(2)])
    assert len(sender.sent) == 2


# -- Telegram client ---------------------------------------------------------------


def client(handler, sleeps=None, clock=None):
    return TelegramClient(
        TOKEN,
        "42",
        transport=httpx.MockTransport(handler),
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
        **({"clock": clock} if clock else {}),
    )


def ok(message_id=1):
    return httpx.Response(200, json={"ok": True, "result": {"message_id": message_id}})


def test_send_payload():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return ok(7)

    assert client(handler).send("<b>hi</b>", [("✅ Applied", "ap:1")]) == 7
    assert seen["path"] == f"/bot{TOKEN}/sendMessage"
    body = seen["body"]
    assert body["chat_id"] == "42" and body["parse_mode"] == "HTML"
    assert body["link_preview_options"] == {"is_disabled": True}
    assert body["reply_markup"] == {
        "inline_keyboard": [[{"text": "✅ Applied", "callback_data": "ap:1"}]]
    }


def test_429_waits_for_retry_after():
    replies = iter(
        [
            httpx.Response(
                429,
                json={
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests: retry after 5",
                    "parameters": {"retry_after": 5},
                },
            ),
            ok(),
        ]
    )
    sleeps: list[float] = []
    client(lambda r: next(replies), sleeps).send("hi")
    assert 5.0 in sleeps


def test_bad_request_is_not_retried_and_hides_the_token():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(
            400, json={"ok": False, "error_code": 400, "description": "Bad Request: can't parse"}
        )

    with pytest.raises(TelegramError) as info:
        client(handler).send("<b>broken")
    assert len(calls) == 1
    assert "SECRET" not in str(info.value) and "can't parse" in str(info.value)


def test_network_errors_never_leak_the_token():
    def handler(request):
        raise httpx.ConnectError(f"failed to reach {request.url}")

    with pytest.raises(TelegramError) as info:
        client(handler).send("hi")
    assert "SECRET" not in str(info.value)


def test_waits_about_a_second_between_messages():
    now = [0.0]
    sleeps: list[float] = []

    def fake_sleep(s):
        sleeps.append(s)
        now[0] += s

    c = TelegramClient(
        TOKEN,
        "42",
        transport=httpx.MockTransport(lambda r: ok()),
        sleep=fake_sleep,
        clock=lambda: now[0],
    )
    c.send("one")
    c.send("two")
    assert sleeps == [pytest.approx(1.1)]


def test_refuses_messages_over_4096_characters():
    with pytest.raises(TelegramError, match="max 4096"):
        client(lambda r: ok()).send("x" * 4097)
