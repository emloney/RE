"""SmartRecruiters and Workday: paginated lists plus a detail call per job."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime

import httpx

from internradar.adapters import get_adapter
from internradar.models import ATS, Company

FRESHWORKS = Company(name="Freshworks", ats=ATS.smartrecruiters, id="Freshworks")
INTEL = Company(
    name="Intel",
    ats=ATS.workday,
    id="intel",
    workday_dc=1,
    workday_site="External",
    workday_search="Bangalore",
)
SR_LIST = "/v1/companies/Freshworks/postings"


def test_smartrecruiters_list_and_details(load_fixture, client_for):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == SR_LIST:
            return httpx.Response(200, json=load_fixture("smartrecruiters_freshworks_list.json"))
        if request.url.path == f"{SR_LIST}/744000157728799":
            return httpx.Response(200, json=load_fixture("smartrecruiters_freshworks_detail.json"))
        return httpx.Response(404)

    adapter = get_adapter(ATS.smartrecruiters, client_for(handler))
    jobs = adapter.list_jobs(FRESHWORKS)

    assert len(jobs) == 3
    job = jobs[0]
    assert job.ats_job_id == "744000157728799"
    assert job.title == "Senior Account Management (EX) - Top Tier NA"
    assert job.location == "Chennai, India"  # "Chennai, , India" tidied
    assert job.url == "https://jobs.smartrecruiters.com/Freshworks/744000157728799"
    assert job.posted_at == datetime(2026, 10, 26, 6, 47, 8, 354000, tzinfo=UTC)
    assert job.needs_details and job.description_text == ""

    full = adapter.fill_details(FRESHWORKS, job)
    assert not full.needs_details
    assert full.url.startswith("https://jobs.smartrecruiters.com/Freshworks/744000157728799-")
    assert full.description_text  # jobDescription + qualifications + additionalInformation


def test_smartrecruiters_paginates(load_fixture, client_for):
    page = load_fixture("smartrecruiters_freshworks_list.json")
    offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        body = copy.deepcopy(page)
        body["totalFound"] = 5
        body["content"] = [{**raw, "id": f"{offset}{i}"} for i, raw in enumerate(page["content"])][
            : 5 - offset
        ]
        return httpx.Response(200, json=body)

    jobs = get_adapter(ATS.smartrecruiters, client_for(handler)).list_jobs(FRESHWORKS)
    assert offsets == [0, 3]
    assert len(jobs) == 5


def test_workday_list_and_details(load_fixture, client_for):
    detail = load_fixture("workday_intel_detail.json")
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        base = "/wday/cxs/intel/External"
        if request.method == "POST" and request.url.path == f"{base}/jobs":
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json=load_fixture("workday_intel_list.json"))
        if request.url.path == f"{base}/job/India-Bangalore/Accountant_JR0283198":
            return httpx.Response(200, json=detail)
        return httpx.Response(404)

    adapter = get_adapter(ATS.workday, client_for(handler))
    jobs = adapter.list_jobs(INTEL)

    assert bodies == [{"limit": 20, "offset": 0, "searchText": "Bangalore", "appliedFacets": {}}]
    assert len(jobs) == 3
    job = jobs[0]
    assert job.title == "Accountant"
    assert job.ats_job_id == "Accountant_JR0283198"
    assert jobs[2].ats_job_id == "CPU-Circuit-Design-Lead_JR0285850-1"
    assert job.location == "India, Bangalore"
    # Our constructed link must equal the externalUrl Workday itself reports.
    assert job.url == detail["jobPostingInfo"]["externalUrl"]
    assert job.needs_details and job.posted_at is None

    full = adapter.fill_details(INTEL, job)
    assert full.posted_at == datetime(2026, 9, 18, tzinfo=UTC)
    assert full.description_text.startswith("Job Details:")
    assert not full.needs_details


def test_workday_uses_first_page_total(load_fixture, client_for):
    """Real Workday quirk: pages after the first report total = 0."""
    page = load_fixture("workday_intel_list.json")
    offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = json.loads(request.content)["offset"]
        offsets.append(offset)
        body = copy.deepcopy(page)
        body["total"] = 25 if offset == 0 else 0
        count = 20 if offset == 0 else 5
        body["jobPostings"] = [
            {**page["jobPostings"][0], "externalPath": f"/job/India-Bangalore/Job_{offset + i}"}
            for i in range(count)
        ]
        return httpx.Response(200, json=body)

    jobs = get_adapter(ATS.workday, client_for(handler)).list_jobs(INTEL)
    assert offsets == [0, 20]
    assert len(jobs) == 25
    assert len({j.id for j in jobs}) == 25


def test_workday_multi_location_left_blank_until_details(load_fixture, client_for):
    page = load_fixture("workday_intel_list.json")
    page["jobPostings"][0]["locationsText"] = "2 Locations"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page)

    jobs = get_adapter(ATS.workday, client_for(handler)).list_jobs(INTEL)
    assert jobs[0].location == ""
    assert jobs[0].needs_details
