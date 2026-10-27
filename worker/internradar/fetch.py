"""Fetch jobs for many companies. One failing company never stops the others."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from internradar.adapters import get_adapter
from internradar.http import PoliteClient
from internradar.models import Company, Job

log = logging.getLogger(__name__)


@dataclass
class CompanyResult:
    company: Company
    jobs: list[Job] = field(default_factory=list)
    error: str | None = None
    duration_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None


def fetch_company(company: Company, http: PoliteClient) -> CompanyResult:
    start = time.monotonic()
    try:
        jobs = get_adapter(company.ats, http).list_jobs(company)
        result = CompanyResult(company, jobs=jobs)
    except Exception as exc:  # noqa: BLE001 - any failure is reported, never raised
        log.warning("%s failed: %s", company.name, exc)
        result = CompanyResult(company, error=f"{type(exc).__name__}: {exc}")
    result.duration_s = time.monotonic() - start
    return result


def fetch_all(companies: list[Company], http: PoliteClient) -> list[CompanyResult]:
    return [fetch_company(c, http) for c in companies if c.enabled]


def fill_details(company: Company, job: Job, http: PoliteClient) -> Job:
    """Fill in the description etc. If that fails, keep the job without it."""
    if not job.needs_details:
        return job
    try:
        return get_adapter(company.ats, http).fill_details(company, job)
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: could not load details for %s: %s", company.name, job.title, exc)
        return job
