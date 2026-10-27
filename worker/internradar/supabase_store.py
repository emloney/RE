"""Store backed by Supabase, talking to its REST API (PostgREST) with httpx.

Uses the service role / secret key, which bypasses Row-Level Security, so this
must only ever run in the worker or serverless functions, never in a browser.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from internradar.detect import KnownJob, SyncPlan, TrackedCompany
from internradar.models import Company
from internradar.store import (
    PendingJob,
    RunSummary,
    company_from_row,
    company_row,
    job_from_row,
    job_row,
)
from internradar.text import parse_dt

log = logging.getLogger(__name__)

PAGE_SIZE = 1000  # Supabase returns at most 1000 rows per request by default
INSERT_CHUNK = 200


class StoreError(Exception):
    pass


class SupabaseStore:
    def __init__(self, url: str, key: str, *, transport: httpx.BaseTransport | None = None) -> None:
        headers = {"apikey": key, "Content-Type": "application/json"}
        # Legacy service_role keys are JWTs ("eyJ..."); new sb_secret_ keys go in apikey only.
        if key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {key}"
        self._client = httpx.Client(
            base_url=f"{url.rstrip('/')}/rest/v1",
            headers=headers,
            timeout=30.0,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    # -- low level ----------------------------------------------------------

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise StoreError(f"{method} {path} failed: {type(exc).__name__}") from exc
        if response.is_error:
            # Never include headers here: they contain the key.
            raise StoreError(f"{method} {path}: HTTP {response.status_code}: {response.text[:300]}")
        return response.json() if response.content else None

    def _select_all(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self._call(
                "GET", f"/{table}", params={**params, "limit": PAGE_SIZE, "offset": offset}
            )
            rows.extend(page)
            if len(page) < PAGE_SIZE:
                return rows
            offset += PAGE_SIZE

    def _rpc(self, fn: str, args: dict[str, Any]) -> Any:
        return self._call("POST", f"/rpc/{fn}", json=args)

    # -- Store interface ----------------------------------------------------

    def sync_companies(self, companies: list[Company]) -> list[str]:
        added: list[str] = []
        for company in companies:  # one by one so a name clash only skips that entry
            try:
                rows = self._call(
                    "POST",
                    "/companies",
                    params={"on_conflict": "ats,ats_id"},
                    json=[company_row(company)],
                    headers={"Prefer": "resolution=ignore-duplicates,return=representation"},
                )
            except StoreError as exc:
                log.warning("could not add %s from companies.yaml: %s", company.name, exc)
                continue
            if rows:
                added.append(company.name)
        return added

    def tracked_companies(self) -> list[TrackedCompany]:
        rows = self._select_all("companies", {"select": "*", "order": "name"})
        return [
            TrackedCompany(
                uuid=row["id"],
                company=company_from_row(row),
                baselined_at=parse_dt(row.get("baselined_at")),
                consecutive_failures=row.get("consecutive_failures") or 0,
            )
            for row in rows
        ]

    def known_jobs(self, company_uuid: str) -> list[KnownJob]:
        rows = self._select_all(
            "jobs",
            {
                "select": "id,dedupe_key,last_seen_at,missing_runs,closed_at",
                "company_id": f"eq.{company_uuid}",
                "order": "id",
            },
        )
        return [
            KnownJob(
                id=row["id"],
                dedupe_key=row["dedupe_key"],
                last_seen_at=parse_dt(row["last_seen_at"]),  # type: ignore[arg-type]
                missing_runs=row["missing_runs"],
                closed=row["closed_at"] is not None,
            )
            for row in rows
        ]

    def start_run(self, trigger: str, now: datetime) -> str:
        rows = self._call(
            "POST",
            "/runs",
            json={"trigger": trigger, "started_at": now.isoformat()},
            headers={"Prefer": "return=representation"},
        )
        return rows[0]["id"]

    def apply_sync(
        self,
        run_id: str,
        plan: SyncPlan,
        company: Company,
        now: datetime,
        duration_s: float,
        filter_rows: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        # 1) Insert new jobs. ignore-duplicates makes this safe to repeat after a crash.
        filter_rows = filter_rows or {}
        rows = [
            job_row(plan, run_id, now, new, filter_rows.get(new.job.id)) for new in plan.inserts
        ]
        for i in range(0, len(rows), INSERT_CHUNK):
            self._call(
                "POST",
                "/jobs",
                params={"on_conflict": "id"},
                json=rows[i : i + INSERT_CHUNK],
                headers={"Prefer": "resolution=ignore-duplicates,return=minimal"},
            )
        # 2) Seen / missing / closed updates + company status, in one transaction.
        self._rpc(
            "apply_company_sync",
            {
                "p_company_id": plan.company_uuid,
                "p_run_id": run_id,
                "p_seen": plan.seen_ids,
                "p_missing": plan.missing_ids,
                "p_closed": plan.close_ids,
                "p_baseline": plan.baseline,
                "p_open_count": plan.open_count,
                "p_duration_ms": int(duration_s * 1000),
                "p_now": now.isoformat(),
            },
        )

    def record_failure(
        self, run_id: str, company_uuid: str, error: str, now: datetime, duration_s: float
    ) -> int:
        return int(
            self._rpc(
                "record_company_failure",
                {
                    "p_company_id": company_uuid,
                    "p_run_id": run_id,
                    "p_error": error,
                    "p_duration_ms": int(duration_s * 1000),
                    "p_now": now.isoformat(),
                },
            )
        )

    def pending_jobs(self, since: datetime) -> list[PendingJob]:
        rows = self._select_all(
            "jobs",
            {
                "select": "*,companies(*)",
                "is_baseline": "is.false",
                "duplicate_of": "is.null",
                "alerted_at": "is.null",
                "scored_at": "is.null",
                "or": "(filter_passed.is.null,filter_passed.is.true)",
                "first_seen_at": f"gte.{since.isoformat()}",
                "order": "first_seen_at",
            },
        )
        pending = []
        for row in rows:
            company_row_ = row.pop("companies")
            company = company_from_row(company_row_)
            pending.append(PendingJob(company_row_["id"], company, job_from_row(row, company.name)))
        return pending

    def save_evaluation(self, job_id: str, fields: dict[str, Any]) -> None:
        self._call(
            "PATCH",
            "/jobs",
            params={"id": f"eq.{job_id}"},
            json=fields,
            headers={"Prefer": "return=minimal"},
        )

    def load_filters(self) -> dict[str, Any] | None:
        rows = self._call("GET", "/settings", params={"select": "filters", "id": "eq.1"})
        return rows[0]["filters"] if rows else None

    def save_filters(self, filters: dict[str, Any]) -> None:
        self._call(
            "PATCH",
            "/settings",
            params={"id": "eq.1"},
            json={"filters": filters, "updated_at": datetime.now(UTC).isoformat()},
            headers={"Prefer": "return=minimal"},
        )

    def finish_run(self, run_id: str, summary: RunSummary, now: datetime) -> None:
        self._call(
            "PATCH",
            "/runs",
            params={"id": f"eq.{run_id}"},
            json={
                "status": summary.status,
                "finished_at": now.isoformat(),
                "companies_checked": summary.companies_checked,
                "companies_failed": summary.companies_failed,
                "new_jobs": summary.new_jobs,
                "alerts_sent": summary.alerts_sent,
                "llm_calls": summary.llm_calls,
                "duration_ms": int(summary.duration_s * 1000),
                "error": summary.error,
            },
            headers={"Prefer": "return=minimal"},
        )
