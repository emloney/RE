"""New-vs-seen rules in the pure planner."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from internradar.detect import (
    KnownJob,
    SuspiciousEmpty,
    TrackedCompany,
    dedupe_key,
    plan_sync,
)
from internradar.models import ATS, Company, Job, make_job_id

NOW = datetime(2026, 10, 27, 2, 30, tzinfo=UTC)
ACME = Company(name="Acme", ats=ATS.lever, id="acme")
BASELINED = TrackedCompany("c1", ACME, baselined_at=NOW - timedelta(days=30))
FIRST_TIME = TrackedCompany("c1", ACME, baselined_at=None)


def job(ats_id: str, title: str = "Data Analyst Intern", location: str = "Bengaluru") -> Job:
    return Job(
        id=make_job_id(ACME.key, ats_id),
        company="Acme",
        ats_job_id=ats_id,
        title=title,
        location=location,
        url=f"https://jobs.lever.co/acme/{ats_id}",
        source_ats=ATS.lever,
    )


def known(j: Job, *, days_ago: float = 0.25, missing: int = 0, closed: bool = False) -> KnownJob:
    return KnownJob(
        id=j.id,
        dedupe_key=dedupe_key(ACME, j.title, j.location),
        last_seen_at=NOW - timedelta(days=days_ago),
        missing_runs=missing,
        closed=closed,
    )


def test_first_fetch_is_a_silent_baseline():
    plan = plan_sync(FIRST_TIME, [job("1"), job("2", "ML Intern")], [], NOW)
    assert plan.baseline
    assert len(plan.inserts) == 2
    assert all(n.is_baseline for n in plan.inserts)
    assert plan.new_jobs == []  # nothing to alert


def test_alert_on_baseline_flag_treats_first_fetch_as_new():
    plan = plan_sync(FIRST_TIME, [job("1")], [], NOW, alert_on_baseline=True)
    assert [n.job.ats_job_id for n in plan.new_jobs] == ["1"]
    assert not plan.inserts[0].is_baseline


def test_only_never_seen_ids_are_new():
    a, b = job("1"), job("2", "ML Intern")
    plan = plan_sync(BASELINED, [a, b], [known(a)], NOW)
    assert plan.seen_ids == [a.id]
    assert [n.job.id for n in plan.new_jobs] == [b.id]


def test_second_identical_run_finds_nothing_new():
    a = job("1")
    first = plan_sync(BASELINED, [a], [], NOW)
    assert len(first.new_jobs) == 1
    second = plan_sync(BASELINED, [a], [known(a)], NOW)
    assert second.new_jobs == [] and second.inserts == []


def test_repost_with_new_id_within_14_days_is_a_duplicate():
    old = job("old", "Data Analyst - Intern", "Bengaluru")
    repost = job("new", "data analyst intern", "bengaluru")  # cosmetic differences
    plan = plan_sync(BASELINED, [repost], [known(old, days_ago=5)], NOW)
    assert plan.inserts[0].duplicate_of == old.id
    assert plan.new_jobs == []


def test_repost_after_14_days_is_new_again():
    old = job("old")
    plan = plan_sync(BASELINED, [job("new")], [known(old, days_ago=20, closed=True)], NOW)
    assert len(plan.new_jobs) == 1


def test_two_identical_new_postings_in_one_run_alert_once():
    plan = plan_sync(BASELINED, [job("1"), job("2")], [], NOW)
    assert len(plan.inserts) == 2
    assert len(plan.new_jobs) == 1
    assert plan.inserts[1].duplicate_of == plan.inserts[0].job.id


def test_missing_job_closes_only_after_two_successful_runs():
    a, b = job("1"), job("2", "ML Intern")
    once = plan_sync(BASELINED, [a], [known(a), known(b, missing=0)], NOW)
    assert once.missing_ids == [b.id] and once.close_ids == []
    twice = plan_sync(BASELINED, [a], [known(a), known(b, missing=1)], NOW)
    assert twice.close_ids == [b.id]


def test_reappearing_job_is_seen_not_new():
    a = job("1")
    plan = plan_sync(BASELINED, [a], [known(a, missing=1)], NOW)
    assert plan.seen_ids == [a.id]  # the store resets missing_runs and reopens it
    assert plan.inserts == []


def test_closed_jobs_are_not_counted_missing_again():
    a, b = job("1"), job("2", "ML Intern")
    plan = plan_sync(BASELINED, [a], [known(a), known(b, missing=2, closed=True)], NOW)
    assert plan.missing_ids == [] and plan.close_ids == []


def test_zero_jobs_when_some_were_open_is_treated_as_an_error():
    with pytest.raises(SuspiciousEmpty):
        plan_sync(BASELINED, [], [known(job("1"))], NOW)


def test_zero_jobs_is_fine_when_nothing_was_open():
    plan = plan_sync(BASELINED, [], [known(job("1"), closed=True)], NOW)
    assert plan.open_count == 0 and plan.close_ids == []


def test_duplicate_listing_in_one_response_is_counted_once():
    a = job("1")
    plan = plan_sync(BASELINED, [a, a], [], NOW)
    assert len(plan.inserts) == 1 and plan.open_count == 1
