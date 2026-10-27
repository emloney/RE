"""SupabaseStore request shapes (mocked). The SQL itself is checked against a
real Postgres in scripts/verify_sql.py."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from internradar.detect import NewJob, SyncPlan
from internradar.models import ATS, Company, Job
from internradar.supabase_store import SupabaseStore

NOW = datetime(2026, 10, 27, 2, 30, tzinfo=UTC)
ACME = Company(name="Acme", ats=ATS.lever, id="acme")


def make(handler, key="sb_secret_abc"):
    return SupabaseStore("https://x.supabase.co/", key, transport=httpx.MockTransport(handler))


def test_auth_headers_for_new_and_legacy_keys():
    seen = []

    def handler(request):
        seen.append(dict(request.headers))
        return httpx.Response(200, json=[])

    make(handler, "sb_secret_abc").tracked_companies()
    make(handler, "eyJhbGciOi.legacy.jwt").tracked_companies()
    assert seen[0]["apikey"] == "sb_secret_abc" and "authorization" not in seen[0]
    assert seen[1]["authorization"] == "Bearer eyJhbGciOi.legacy.jwt"


def test_select_paginates_past_1000_rows():
    offsets = []

    def handler(request):
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        count = 1000 if offset == 0 else 3
        rows = [
            {
                "id": f"j{offset + i}",
                "dedupe_key": "k",
                "last_seen_at": "2026-10-27T00:00:00+00:00",
                "missing_runs": 0,
                "closed_at": None,
            }
            for i in range(count)
        ]
        return httpx.Response(200, json=rows)

    jobs = make(handler).known_jobs("c1")
    assert offsets == [0, 1000]
    assert len(jobs) == 1003


def test_apply_sync_inserts_then_calls_rpc():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, request.url.params, request))
        return httpx.Response(201 if request.method == "POST" else 200)

    job = Job(
        id="j1",
        company="Acme",
        ats_job_id="1",
        title="ML Intern",
        location="Bengaluru",
        url="https://jobs.lever.co/acme/1",
        source_ats=ATS.lever,
    )
    plan = SyncPlan(
        company_uuid="c1",
        baseline=False,
        inserts=[NewJob(job, "lever:acme|ml intern|bengaluru", is_baseline=False)],
        seen_ids=["j0"],
        close_ids=["jx"],
        open_count=2,
    )
    make(handler).apply_sync("r1", plan, ACME, NOW, 1.5)

    (m1, p1, q1, r1), (m2, p2, _, r2) = calls
    assert (m1, p1, q1["on_conflict"]) == ("POST", "/rest/v1/jobs", "id")
    assert "ignore-duplicates" in r1.headers["prefer"]
    row = json.loads(r1.content)[0]
    assert row["first_seen_at"] == NOW.isoformat() and row["title_norm"] == "ml intern"
    assert (m2, p2) == ("POST", "/rest/v1/rpc/apply_company_sync")
    args = json.loads(r2.content)
    assert args["p_seen"] == ["j0"] and args["p_closed"] == ["jx"] and args["p_duration_ms"] == 1500


def test_pending_jobs_query_and_parsing():
    seen = {}

    def handler(request):
        seen.update(request.url.params)
        return httpx.Response(
            200,
            json=[
                {
                    "id": "j1",
                    "ats_job_id": "744",
                    "title": "Data Analyst",
                    "location": "Bengaluru",
                    "is_remote": False,
                    "department": None,
                    "url": "https://jobs.smartrecruiters.com/Freshworks/744",
                    "posted_at": "2026-10-26T06:47:08+00:00",
                    "description_text": "",
                    "source_ats": "smartrecruiters",
                    "companies": {
                        "id": "c1",
                        "name": "Freshworks",
                        "ats": "smartrecruiters",
                        "ats_id": "Freshworks",
                        "enabled": True,
                    },
                }
            ],
        )

    (p,) = make(handler).pending_jobs(NOW)
    assert seen["scored_at"] == "is.null" and seen["is_baseline"] == "is.false"
    assert seen["or"] == "(filter_passed.is.null,filter_passed.is.true)"
    assert seen["select"] == "*,companies(*)"
    assert p.company_uuid == "c1" and p.company.name == "Freshworks"
    assert p.job.needs_details  # empty description on a list-only ATS


def test_errors_never_include_the_key():
    def handler(request):
        return httpx.Response(401, json={"message": "Invalid API key"})

    try:
        make(handler, "sb_secret_TOPSECRET").tracked_companies()
    except Exception as exc:  # noqa: BLE001
        assert "TOPSECRET" not in str(exc)
        assert "401" in str(exc)
    else:
        raise AssertionError("expected StoreError")
