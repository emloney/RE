"""PoliteClient: retries, backoff, rate limiting, User-Agent."""

from __future__ import annotations

import httpx
import pytest

from internradar.http import FetchError, PoliteClient


def _client(handler, sleeps: list[float], clock=None) -> PoliteClient:
    return PoliteClient(
        "me@example.com",
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
        **({"clock": clock} if clock else {}),
    )


def test_user_agent_includes_contact():
    agents: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        agents.append(request.headers["User-Agent"])
        return httpx.Response(200, json={})

    _client(handler, []).get_json("https://a.example/x")
    assert agents == ["InternRadar/1.0 (personal job alerts; contact: me@example.com)"]


def test_retries_429_using_retry_after_then_succeeds():
    responses = iter(
        [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json=1)]
    )
    sleeps: list[float] = []
    assert _client(lambda r: next(responses), sleeps).get_json("https://a.example/x") == 1
    assert 7.0 in sleeps


def test_retries_5xx_with_exponential_backoff_then_gives_up():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    sleeps: list[float] = []
    with pytest.raises(FetchError, match="gave up"):
        _client(handler, sleeps).get_json("https://a.example/x")
    assert len(calls) == 4  # 1 try + 3 retries
    assert [s for s in sleeps if s >= 1] == [1.0, 2.0, 4.0]


def test_404_is_not_retried():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404)

    with pytest.raises(FetchError) as info:
        _client(handler, []).get_json("https://a.example/x")
    assert info.value.status == 404
    assert len(calls) == 1


def test_timeout_is_retried():
    attempts = iter([httpx.ReadTimeout("slow"), None])

    def handler(request: httpx.Request) -> httpx.Response:
        exc = next(attempts)
        if exc:
            raise exc
        return httpx.Response(200, json="ok")

    assert _client(handler, []).get_json("https://a.example/x") == "ok"


def test_rate_limit_is_per_domain():
    now = [100.0]
    sleeps: list[float] = []

    def fake_sleep(s: float) -> None:
        sleeps.append(s)
        now[0] += s

    client = PoliteClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
        sleep=fake_sleep,
        clock=lambda: now[0],
    )
    client.get_json("https://a.example/1")
    client.get_json("https://b.example/1")  # different domain: no wait
    assert sleeps == []
    client.get_json("https://a.example/2")  # same domain, 0s later: wait ~1s
    assert sleeps == [pytest.approx(1.0)]
