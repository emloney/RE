"""One checker run: sync companies, fetch each one, detect changes, save state."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from internradar.detect import NewJob, SuspiciousEmpty, TrackedCompany, plan_sync
from internradar.fetch import fetch_company
from internradar.http import PoliteClient
from internradar.models import Company
from internradar.store import RunSummary, Store

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class FoundJob:
    company: TrackedCompany
    new: NewJob


@dataclass
class RunResult:
    summary: RunSummary
    new_jobs: list[FoundJob] = field(default_factory=list)
    added_companies: list[str] = field(default_factory=list)
    # Companies whose failure streak just reached a warning threshold (Phase 5 alerts on this).
    failure_streaks: dict[str, int] = field(default_factory=dict)


class UnknownCompany(Exception):
    pass


def run_once(
    store: Store,
    yaml_companies: list[Company],
    http: PoliteClient,
    *,
    only: str | None = None,
    alert_on_baseline: bool = False,
    trigger: str = "manual",
    now: Callable[[], datetime] = utcnow,
) -> RunResult:
    start = time.monotonic()
    summary = RunSummary(trigger=trigger, started_at=now())
    result = RunResult(summary=summary)

    result.added_companies = store.sync_companies(yaml_companies)
    if result.added_companies:
        log.info("new companies from companies.yaml: %s", ", ".join(result.added_companies))

    tracked = store.tracked_companies()
    if only:
        tracked = [t for t in tracked if t.company.name.lower() == only.lower()]
        if not tracked:
            raise UnknownCompany(f"no tracked company named {only!r}")
    else:
        tracked = [t for t in tracked if t.company.enabled]

    run_id = store.start_run(trigger, now())
    try:
        for t in tracked:
            _check_company(store, run_id, t, http, result, alert_on_baseline, now)
    except Exception as exc:
        summary.status = "failed"
        summary.error = f"{type(exc).__name__}: {exc}"
        summary.duration_s = time.monotonic() - start
        store.finish_run(run_id, summary, now())
        raise

    summary.new_jobs = len(result.new_jobs)
    if summary.companies_checked and summary.companies_failed == summary.companies_checked:
        summary.status = "failed"
    elif summary.companies_failed:
        summary.status = "partial"
    else:
        summary.status = "success"
    summary.duration_s = time.monotonic() - start
    store.finish_run(run_id, summary, now())
    return result


def _check_company(
    store: Store,
    run_id: str,
    tracked: TrackedCompany,
    http: PoliteClient,
    result: RunResult,
    alert_on_baseline: bool,
    now: Callable[[], datetime],
) -> None:
    summary = result.summary
    summary.companies_checked += 1
    fetched = fetch_company(tracked.company, http)
    error = fetched.error
    plan = None
    if error is None:
        try:
            plan = plan_sync(
                tracked,
                fetched.jobs,
                store.known_jobs(tracked.uuid),
                now(),
                alert_on_baseline=alert_on_baseline,
            )
        except SuspiciousEmpty as exc:
            error = str(exc)

    if plan is None:
        summary.companies_failed += 1
        summary.failures.append((tracked.company.name, error or "unknown error"))
        streak = store.record_failure(
            run_id, tracked.uuid, error or "unknown error", now(), fetched.duration_s
        )
        if streak >= 3:
            result.failure_streaks[tracked.company.name] = streak
        log.warning("%s: %s", tracked.company.name, error)
        return

    store.apply_sync(run_id, plan, tracked.company, now(), fetched.duration_s)
    result.new_jobs.extend(FoundJob(tracked, n) for n in plan.new_jobs)
    label = f"{len(plan.new_jobs)} new"
    if plan.baseline:
        label = (
            "baseline, treated as new (--alert-on-baseline)" if alert_on_baseline else "baseline"
        )
    log.info(
        "%s: %d jobs, %s, %d re-posts, %d missing, %d closed",
        tracked.company.name,
        plan.open_count,
        label,
        sum(1 for n in plan.inserts if n.duplicate_of),
        len(plan.missing_ids),
        len(plan.close_ids),
    )


def format_summary(summary: RunSummary) -> str:
    lines = [
        f"Run {summary.status}: {summary.companies_checked} companies checked, "
        f"{summary.companies_failed} failed, {summary.new_jobs} new jobs, "
        f"{summary.alerts_sent} alerts sent, {summary.llm_calls} LLM calls, "
        f"{summary.duration_s:.1f}s"
    ]
    lines += [f"  failed: {name} — {err}" for name, err in summary.failures]
    return "\n".join(lines)
