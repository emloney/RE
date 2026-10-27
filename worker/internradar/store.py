"""Where state lives. The worker talks to a Store; tests use MemoryStore,
real runs use SupabaseStore (see supabase_store.py), --dry-run wraps either in
ReadOnlyStore so nothing is written.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from internradar.detect import KnownJob, NewJob, SyncPlan, TrackedCompany
from internradar.models import ATS, Company, Job
from internradar.text import normalise_title, parse_dt

DESCRIPTION_LIMIT = 20_000  # characters stored per job (the LLM only sees ~6k)
# ATSs whose list call has no description: an empty one means "fetch details".
DETAILS_ATS = {ATS.workday, ATS.smartrecruiters}


@dataclass(frozen=True)
class PendingJob:
    """A new job that still needs filtering/scoring (e.g. after a crash mid-run)."""

    company_uuid: str
    company: Company
    job: Job


@dataclass
class RunSummary:
    trigger: str
    started_at: datetime
    status: str = "running"  # running | success | partial | failed
    companies_checked: int = 0
    companies_failed: int = 0
    new_jobs: int = 0
    alerts_sent: int = 0
    llm_calls: int = 0
    duration_s: float = 0.0
    error: str | None = None
    failures: list[tuple[str, str]] = field(default_factory=list)  # (company, error)


class Store(Protocol):
    def sync_companies(self, companies: list[Company]) -> list[str]:
        """Insert yaml companies that aren't in the store yet. Returns names added."""

    def tracked_companies(self) -> list[TrackedCompany]: ...

    def known_jobs(self, company_uuid: str) -> list[KnownJob]: ...

    def start_run(self, trigger: str, now: datetime) -> str: ...

    def apply_sync(
        self,
        run_id: str,
        plan: SyncPlan,
        company: Company,
        now: datetime,
        duration_s: float,
        filter_rows: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Save new jobs (with their rule-filter result) and seen/missing/closed updates."""

    def record_failure(
        self, run_id: str, company_uuid: str, error: str, now: datetime, duration_s: float
    ) -> int:
        """Returns the company's consecutive failure count after this one."""

    def finish_run(self, run_id: str, summary: RunSummary, now: datetime) -> None: ...

    def pending_jobs(self, since: datetime) -> list[PendingJob]:
        """New (non-baseline, non-duplicate) jobs since `since` that were never scored
        or alerted and weren't filtered out."""

    def save_evaluation(self, job_id: str, fields: dict[str, Any]) -> None: ...

    def load_filters(self) -> dict[str, Any] | None: ...

    def save_filters(self, filters: dict[str, Any]) -> None: ...


def job_row(
    plan: SyncPlan,
    run_id: str,
    now: datetime,
    new: NewJob,
    filter_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Database row for a newly seen job (shared by both stores)."""
    job = new.job
    # Every row must have the same keys: PostgREST rejects bulk inserts that don't.
    filters = {"filter_passed": None, "filter_reason": None, "category": None}
    filters.update(filter_row or {})
    return {
        **filters,
        "id": job.id,
        "company_id": plan.company_uuid,
        "ats_job_id": job.ats_job_id,
        "title": job.title,
        "title_norm": normalise_title(job.title),
        "location": job.location,
        "is_remote": job.is_remote,
        "department": job.department,
        "url": job.url,
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "first_seen_at": now.isoformat(),
        "first_seen_run": run_id,
        "last_seen_at": now.isoformat(),
        "description_text": job.description_text[:DESCRIPTION_LIMIT],
        "source_ats": job.source_ats.value,
        "dedupe_key": new.dedupe_key,
        "duplicate_of": new.duplicate_of,
        "is_baseline": new.is_baseline,
    }


def company_row(company: Company) -> dict[str, Any]:
    return {
        "name": company.name,
        "ats": company.ats.value,
        "ats_id": company.id,
        "workday_dc": company.workday_dc,
        "workday_site": company.workday_site,
        "workday_search": company.workday_search,
        "careers_url": company.careers_url,
        "html_selectors": (
            company.html_selectors.model_dump(exclude_none=True) if company.html_selectors else None
        ),
        "enabled": company.enabled,
        "source": "yaml",
    }


def job_from_row(row: dict[str, Any], company_name: str) -> Job:
    ats = ATS(row["source_ats"])
    description = row.get("description_text") or ""
    posted = row.get("posted_at")
    return Job(
        id=row["id"],
        company=company_name,
        ats_job_id=row.get("ats_job_id"),
        title=row["title"],
        location=row.get("location") or "",
        is_remote=bool(row.get("is_remote")),
        department=row.get("department"),
        url=row["url"],
        posted_at=posted if isinstance(posted, datetime) else parse_dt(posted),
        description_text=description,
        source_ats=ats,
        needs_details=not description and ats in DETAILS_ATS,
    )


def evaluation_row(job: Job) -> dict[str, Any]:
    """Fields a details fetch may have filled in, to save alongside the evaluation."""
    return {
        "location": job.location,
        "is_remote": job.is_remote,
        "url": job.url,
        "posted_at": job.posted_at.isoformat() if job.posted_at else None,
        "description_text": job.description_text[:DESCRIPTION_LIMIT],
    }


def company_from_row(row: dict[str, Any]) -> Company:
    return Company.model_validate(
        {
            "name": row["name"],
            "ats": row["ats"],
            "id": row["ats_id"],
            "workday_dc": row.get("workday_dc"),
            "workday_site": row.get("workday_site"),
            "workday_search": row.get("workday_search"),
            "careers_url": row.get("careers_url"),
            "html_selectors": row.get("html_selectors"),
            "enabled": row.get("enabled", True),
        }
    )


class MemoryStore:
    """In-memory store that mirrors the SQL functions. Used by tests and by
    --dry-run when no Supabase is configured."""

    def __init__(self) -> None:
        self.companies: dict[str, dict[str, Any]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.fetches: list[dict[str, Any]] = []
        self.filters: dict[str, Any] | None = None

    def sync_companies(self, companies: list[Company]) -> list[str]:
        added = []
        for c in companies:
            clash = any(
                row["name"] == c.name or (row["ats"], row["ats_id"]) == (c.ats.value, c.id)
                for row in self.companies.values()
            )
            if not clash:
                self.companies[str(uuid.uuid4())] = {
                    **company_row(c),
                    "baselined_at": None,
                    "consecutive_failures": 0,
                    "last_error": None,
                    "open_jobs_count": 0,
                }
                added.append(c.name)
        return added

    def tracked_companies(self) -> list[TrackedCompany]:
        return [
            TrackedCompany(
                uuid=cid,
                company=company_from_row(row),
                baselined_at=row["baselined_at"],
                consecutive_failures=row["consecutive_failures"],
            )
            for cid, row in sorted(self.companies.items(), key=lambda kv: kv[1]["name"])
        ]

    def known_jobs(self, company_uuid: str) -> list[KnownJob]:
        return [
            KnownJob(
                id=j["id"],
                dedupe_key=j["dedupe_key"],
                last_seen_at=j["last_seen_at"],
                missing_runs=j["missing_runs"],
                closed=j["closed_at"] is not None,
            )
            for j in self.jobs.values()
            if j["company_id"] == company_uuid
        ]

    def start_run(self, trigger: str, now: datetime) -> str:
        run_id = str(uuid.uuid4())
        self.runs[run_id] = {"trigger": trigger, "status": "running", "started_at": now}
        return run_id

    def apply_sync(
        self,
        run_id: str,
        plan: SyncPlan,
        company: Company,
        now: datetime,
        duration_s: float,
        filter_rows: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        for new in plan.inserts:
            if new.job.id not in self.jobs:
                row = job_row(plan, run_id, now, new, (filter_rows or {}).get(new.job.id))
                row.update(
                    first_seen_at=now,
                    last_seen_at=now,
                    missing_runs=0,
                    closed_at=None,
                    alerted_at=None,
                    scored_at=None,
                )
                self.jobs[new.job.id] = row
        for jid in plan.seen_ids:
            self.jobs[jid].update(last_seen_at=now, missing_runs=0, closed_at=None)
        for jid in plan.missing_ids:
            if self.jobs[jid]["closed_at"] is None:
                self.jobs[jid]["missing_runs"] += 1
        for jid in plan.close_ids:
            if self.jobs[jid]["closed_at"] is None:
                self.jobs[jid]["missing_runs"] += 1
                self.jobs[jid]["closed_at"] = now
        row = self.companies[plan.company_uuid]
        row.update(
            last_success_at=now,
            last_error=None,
            consecutive_failures=0,
            open_jobs_count=plan.open_count,
        )
        if plan.baseline and row["baselined_at"] is None:
            row["baselined_at"] = now
        self.fetches.append({"run_id": run_id, "company_id": plan.company_uuid, "ok": True})

    def record_failure(
        self, run_id: str, company_uuid: str, error: str, now: datetime, duration_s: float
    ) -> int:
        row = self.companies[company_uuid]
        row["consecutive_failures"] += 1
        row.update(last_error=error, last_error_at=now)
        self.fetches.append({"run_id": run_id, "company_id": company_uuid, "ok": False})
        return row["consecutive_failures"]

    def finish_run(self, run_id: str, summary: RunSummary, now: datetime) -> None:
        self.runs[run_id].update(status=summary.status, finished_at=now, summary=summary)

    def pending_jobs(self, since: datetime) -> list[PendingJob]:
        pending = []
        for row in sorted(self.jobs.values(), key=lambda r: r["first_seen_at"]):
            if (
                not row["is_baseline"]
                and row["duplicate_of"] is None
                and row["alerted_at"] is None
                and row["scored_at"] is None
                and row["filter_passed"] is not False
                and row["first_seen_at"] >= since
            ):
                company = company_from_row(self.companies[row["company_id"]])
                pending.append(
                    PendingJob(row["company_id"], company, job_from_row(row, company.name))
                )
        return pending

    def save_evaluation(self, job_id: str, fields: dict[str, Any]) -> None:
        self.jobs[job_id].update(fields)

    def load_filters(self) -> dict[str, Any] | None:
        return self.filters

    def save_filters(self, filters: dict[str, Any]) -> None:
        self.filters = filters


class ReadOnlyStore:
    """Reads go to the real store; writes are skipped. Used for --dry-run."""

    def __init__(self, inner: Store) -> None:
        self.inner = inner

    def sync_companies(self, companies: list[Company]) -> list[str]:
        return []

    def tracked_companies(self) -> list[TrackedCompany]:
        return self.inner.tracked_companies()

    def known_jobs(self, company_uuid: str) -> list[KnownJob]:
        return self.inner.known_jobs(company_uuid)

    def start_run(self, trigger: str, now: datetime) -> str:
        return "dry-run"

    def apply_sync(self, *args: Any, **kwargs: Any) -> None:
        return None

    def record_failure(self, *args: Any, **kwargs: Any) -> int:
        return 0

    def finish_run(self, *args: Any, **kwargs: Any) -> None:
        return None

    def pending_jobs(self, since: datetime) -> list[PendingJob]:
        return self.inner.pending_jobs(since)

    def save_evaluation(self, *args: Any, **kwargs: Any) -> None:
        return None

    def load_filters(self) -> dict[str, Any] | None:
        return self.inner.load_filters()

    def save_filters(self, *args: Any, **kwargs: Any) -> None:
        return None
