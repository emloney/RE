"""Greenhouse, Lever and Ashby: one call returns everything."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from internradar.adapters import get_adapter
from internradar.models import ATS, Company, make_job_id


def test_greenhouse(load_fixture, client_for):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=load_fixture("greenhouse_groww.json"))

    company = Company(name="Groww", ats=ATS.greenhouse, id="groww")
    jobs = get_adapter(ATS.greenhouse, client_for(handler)).list_jobs(company)

    assert seen == ["https://boards-api.greenhouse.io/v1/boards/groww/jobs?content=true"]
    assert len(jobs) == 3
    job = jobs[0]
    assert job.id == make_job_id("greenhouse:groww", "4970739101")
    assert job.ats_job_id == "4970739101"
    assert job.title == "Assistant Manager - Internal Audit"
    assert job.location == "Bengaluru-VTP, India"
    assert job.department == "Internal Controls"
    assert job.url == "https://job-boards.eu.greenhouse.io/groww/jobs/4970739101"
    assert job.posted_at == datetime(2026, 9, 14, 7, 49, 28, tzinfo=UTC)
    assert job.source_ats is ATS.greenhouse
    assert not job.is_remote
    # Greenhouse content is double-escaped HTML; we must end up with plain text.
    assert job.description_text.startswith("About Groww")
    assert "<" not in job.description_text and "&lt;" not in job.description_text
    assert jobs[2].title == "Creative Illustrator"  # trailing space stripped


def test_lever(load_fixture, client_for):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v0/postings/meesho"
        assert request.url.params["mode"] == "json"
        return httpx.Response(200, json=load_fixture("lever_meesho.json"))

    company = Company(name="Meesho", ats=ATS.lever, id="meesho")
    jobs = get_adapter(ATS.lever, client_for(handler)).list_jobs(company)

    assert len(jobs) == 3
    job = jobs[0]
    assert job.ats_job_id == "7d9af9b5-c1c7-48ec-bbb5-9b25e49f6596"
    assert job.title == "AM/ Manager - Risk & Decision Science"
    assert job.location == "Bangalore, Karnataka"  # allLocations duplicate not repeated
    assert job.department == "Financial Services"
    assert job.url == "https://jobs.lever.co/meesho/7d9af9b5-c1c7-48ec-bbb5-9b25e49f6596"
    assert job.posted_at == datetime.fromtimestamp(1757916149833 / 1000, tz=UTC)
    assert job.description_text.startswith("About the Team")
    assert "What you will do" in job.description_text  # from the "lists" blocks
    assert not job.is_remote


def test_ashby(load_fixture, client_for):
    data = load_fixture("ashby_sarvam.json")
    data["jobs"][2]["isListed"] = False  # unlisted jobs must be skipped

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.ashbyhq.com/posting-api/job-board/sarvam"
        return httpx.Response(200, json=data)

    company = Company(name="Sarvam AI", ats=ATS.ashby, id="sarvam")
    jobs = get_adapter(ATS.ashby, client_for(handler)).list_jobs(company)

    assert [j.title for j in jobs] == [
        "Engagement Manager, Chanakya",
        "Data Scientist - Evaluations, Chanakya",
    ]
    job = jobs[1]
    assert job.location == "Bengaluru"
    assert job.department == "Engineering"
    assert job.url == "https://jobs.ashbyhq.com/sarvam/7c42207a-bb56-413c-839f-9ad692df840e"
    assert job.posted_at is not None and job.posted_at.year == 2026
    assert job.description_text
    assert not job.is_remote
