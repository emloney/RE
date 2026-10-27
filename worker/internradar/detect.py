"""Decide what changed for one company: new jobs, re-posts, seen, missing, closed.

This module is pure (no network, no database), so every rule is unit-tested:

- New = the job id has never been seen for this company.
- First successful fetch of a company = BASELINE: save everything, alert nothing.
- Re-post = new id but same company + normalised title + location as a job seen
  in the last 14 days. Saved with duplicate_of set, no alert.
- A job is closed only after it is missing from 2 consecutive SUCCESSFUL fetches.
- A fetch that suddenly returns 0 jobs (when some were open) is treated as an
  error, so nothing gets marked missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from internradar.models import Company, Job
from internradar.text import normalise_location, normalise_title

DEDUPE_WINDOW = timedelta(days=14)
CLOSE_AFTER_MISSING = 2


@dataclass(frozen=True)
class TrackedCompany:
    """A company row from the database."""

    uuid: str
    company: Company
    baselined_at: datetime | None = None
    consecutive_failures: int = 0


@dataclass(frozen=True)
class KnownJob:
    """The little we need to know about a job already in the database."""

    id: str
    dedupe_key: str
    last_seen_at: datetime
    missing_runs: int = 0
    closed: bool = False


@dataclass(frozen=True)
class NewJob:
    job: Job
    dedupe_key: str
    is_baseline: bool
    duplicate_of: str | None = None

    @property
    def alertable(self) -> bool:
        return not self.is_baseline and self.duplicate_of is None


@dataclass
class SyncPlan:
    company_uuid: str
    baseline: bool
    inserts: list[NewJob] = field(default_factory=list)
    seen_ids: list[str] = field(default_factory=list)
    missing_ids: list[str] = field(default_factory=list)  # missing once more, still open
    close_ids: list[str] = field(default_factory=list)  # missing long enough: close now
    open_count: int = 0

    @property
    def new_jobs(self) -> list[NewJob]:
        """Genuinely new jobs (not baseline, not re-posts): candidates for alerts."""
        return [n for n in self.inserts if n.alertable]


class SuspiciousEmpty(Exception):
    """The ATS returned no jobs although the company had open jobs last time."""


def dedupe_key(company: Company, title: str, location: str) -> str:
    return f"{company.key}|{normalise_title(title)}|{normalise_location(location)}"


def plan_sync(
    tracked: TrackedCompany,
    fetched: list[Job],
    known: list[KnownJob],
    now: datetime,
    *,
    alert_on_baseline: bool = False,
) -> SyncPlan:
    by_id = {k.id: k for k in known}
    open_before = [k for k in known if not k.closed]
    if not fetched and open_before:
        raise SuspiciousEmpty(
            f"returned 0 jobs but {len(open_before)} were open last time; "
            "treating it as a temporary error"
        )

    baseline = tracked.baselined_at is None
    check_reposts = not baseline or alert_on_baseline
    recent: dict[str, str] = {}
    for k in known:
        if now - k.last_seen_at <= DEDUPE_WINDOW:
            recent.setdefault(k.dedupe_key, k.id)

    plan = SyncPlan(company_uuid=tracked.uuid, baseline=baseline)
    fetched_ids: set[str] = set()
    for job in fetched:
        if job.id in fetched_ids:  # an ATS occasionally lists the same job twice
            continue
        fetched_ids.add(job.id)
        if job.id in by_id:
            plan.seen_ids.append(job.id)
            continue
        key = dedupe_key(tracked.company, job.title, job.location)
        plan.inserts.append(
            NewJob(
                job=job,
                dedupe_key=key,
                is_baseline=baseline and not alert_on_baseline,
                duplicate_of=recent.get(key) if check_reposts else None,
            )
        )
        recent.setdefault(key, job.id)

    for k in open_before:
        if k.id in fetched_ids:
            continue
        if k.missing_runs + 1 >= CLOSE_AFTER_MISSING:
            plan.close_ids.append(k.id)
        else:
            plan.missing_ids.append(k.id)

    plan.open_count = len(fetched_ids)
    return plan
