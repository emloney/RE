"""Whole runs against MemoryStore with fake ATS responses: baseline, new jobs,
idempotency, failed fetches and closing jobs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from internradar.models import ATS, Company
from internradar.run import run_once
from internradar.store import MemoryStore, ReadOnlyStore

ACME = Company(name="Acme", ats=ATS.lever, id="acme")
OTHER = Company(name="Other", ats=ATS.lever, id="other")


def posting(pid: str, title: str) -> dict:
    return {
        "id": pid,
        "text": title,
        "categories": {"location": "Bengaluru"},
        "hostedUrl": f"https://jobs.lever.co/acme/{pid}",
        "createdAt": 1790000000000,
        "descriptionPlain": "desc",
    }


class FakeLever:
    """Serves whatever postings each company currently has; 500 if marked broken."""

    def __init__(self) -> None:
        self.boards: dict[str, list[dict]] = {"acme": [], "other": []}
        self.broken: set[str] = set()

    def handler(self, request: httpx.Request) -> httpx.Response:
        slug = request.url.path.rsplit("/", 1)[-1]
        if slug in self.broken:
            return httpx.Response(500)
        return httpx.Response(200, json=self.boards[slug])


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 10, 27, 2, 30, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, hours: float = 4) -> None:
        self.t += timedelta(hours=hours)


def setup(client_for):
    lever, store, clock = FakeLever(), MemoryStore(), Clock()
    http = client_for(lever.handler)

    def go(**kwargs):
        clock.advance()
        return run_once(store, [ACME, OTHER], http, now=clock, **kwargs)

    return lever, store, go


def new_titles(result) -> list[str]:
    return sorted(f.new.job.title for f in result.new_jobs)


def test_baseline_then_new_job_then_idempotent(client_for):
    lever, store, go = setup(client_for)
    lever.boards["acme"] = [posting("1", "Old Job A"), posting("2", "Old Job B")]

    first = go()
    assert first.added_companies == ["Acme", "Other"]
    assert first.new_jobs == []  # baseline: hundreds of old jobs, zero alerts
    assert len(store.jobs) == 2 and all(j["is_baseline"] for j in store.jobs.values())

    lever.boards["acme"].append(posting("3", "Data Analyst Intern"))
    second = go()
    assert new_titles(second) == ["Data Analyst Intern"]
    assert second.summary.new_jobs == 1 and second.summary.status == "success"

    third = go()  # nothing changed: nothing new, no duplicates
    assert third.new_jobs == []
    assert len(store.jobs) == 3


def test_newly_added_company_is_baselined_on_its_own(client_for):
    lever, store, go = setup(client_for)
    go()  # Acme and Other baselined (empty)
    late = Company(name="Late", ats=ATS.lever, id="late")
    lever.boards["late"] = [posting("9", "Existing Role")]

    clock_now = datetime(2026, 10, 28, tzinfo=UTC)
    result = run_once(store, [ACME, OTHER, late], client_for(lever.handler), now=lambda: clock_now)
    assert result.added_companies == ["Late"]
    assert result.new_jobs == []


def test_failed_fetch_never_closes_jobs_and_counts_streak(client_for):
    lever, store, go = setup(client_for)
    lever.boards["acme"] = [posting("1", "Role")]
    go()
    lever.broken.add("acme")

    for _ in range(3):
        result = go()
        assert result.summary.companies_failed == 1
        assert result.summary.status == "partial"  # Other still worked
    assert result.failure_streaks == {"Acme": 3}
    job = next(iter(store.jobs.values()))
    assert job["missing_runs"] == 0 and job["closed_at"] is None

    lever.broken.clear()
    go()
    acme = next(r for r in store.companies.values() if r["name"] == "Acme")
    assert acme["consecutive_failures"] == 0


def test_sudden_empty_response_is_not_trusted(client_for):
    lever, store, go = setup(client_for)
    lever.boards["acme"] = [posting("1", "Role")]
    go()
    lever.boards["acme"] = []
    result = go()
    assert result.summary.failures[0][0] == "Acme"
    assert next(iter(store.jobs.values()))["missing_runs"] == 0


def test_job_closes_after_two_successful_runs_missing(client_for):
    lever, store, go = setup(client_for)
    lever.boards["acme"] = [posting("1", "Stays"), posting("2", "Goes")]
    go()
    lever.boards["acme"] = [posting("1", "Stays")]
    go()
    goes = next(j for j in store.jobs.values() if j["title"] == "Goes")
    assert goes["missing_runs"] == 1 and goes["closed_at"] is None
    go()
    assert goes["closed_at"] is not None


def test_dry_run_writes_nothing(client_for):
    lever, store, go = setup(client_for)
    go()
    lever.boards["acme"] = [posting("5", "Fresh Role")]
    result = run_once(ReadOnlyStore(store), [ACME, OTHER], client_for(lever.handler))
    assert new_titles(result) == ["Fresh Role"]
    assert store.jobs == {}  # still nothing saved
    assert new_titles(go()) == ["Fresh Role"]  # the real run still alerts it


def test_all_companies_failing_marks_run_failed(client_for):
    lever, store, go = setup(client_for)
    lever.broken.update({"acme", "other"})
    result = go()
    assert result.summary.status == "failed"
    assert {name for name, _ in result.summary.failures} == {"Acme", "Other"}


def test_disabled_companies_are_skipped_unless_named(client_for):
    lever, store, go = setup(client_for)
    go()
    acme_id = next(k for k, r in store.companies.items() if r["name"] == "Acme")
    store.companies[acme_id]["enabled"] = False
    assert go().summary.companies_checked == 1
    assert go(only="acme").summary.companies_checked == 1
