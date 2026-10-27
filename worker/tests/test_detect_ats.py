"""detect_ats URL patterns (no network) and the cheap probe() calls."""

from __future__ import annotations

import json

import httpx
import pytest

from internradar.adapters import get_adapter
from internradar.detect_ats import best_guess, detect
from internradar.models import ATS, Company


@pytest.mark.parametrize(
    ("url", "ats", "board"),
    [
        ("https://job-boards.greenhouse.io/databricks", ATS.greenhouse, "databricks"),
        ("https://job-boards.eu.greenhouse.io/groww/jobs/4970739101", ATS.greenhouse, "groww"),
        ("https://boards.greenhouse.io/embed/job_board?for=stripe", ATS.greenhouse, "stripe"),
        ("https://boards-api.greenhouse.io/v1/boards/okta/jobs", ATS.greenhouse, "okta"),
        ("https://jobs.lever.co/meesho/7d9af9b5-c1c7", ATS.lever, "meesho"),
        ("https://jobs.ashbyhq.com/sarvam", ATS.ashby, "sarvam"),
        (
            "https://jobs.smartrecruiters.com/Freshworks/744000157728799",
            ATS.smartrecruiters,
            "Freshworks",
        ),
        ("https://careers.smartrecruiters.com/Swiggy", ATS.smartrecruiters, "Swiggy"),
    ],
)
def test_url_patterns(url, ats, board):
    guess = best_guess(url)
    assert guess is not None
    assert (guess.ats, guess.id) == (ats, board)


@pytest.mark.parametrize(
    "url",
    [
        "https://intel.wd1.myworkdayjobs.com/External",
        "https://intel.wd1.myworkdayjobs.com/en-US/External/job/India-Bangalore/Accountant_JR0283198",
    ],
)
def test_workday_pattern(url):
    guess = best_guess(url)
    assert guess is not None
    assert (guess.ats, guess.id, guess.workday_dc, guess.workday_site) == (
        ATS.workday,
        "intel",
        1,
        "External",
    )


def test_detect_scans_page_html_when_url_has_no_ats(client_for):
    page = """<html><a href="https://jobs.lever.co/acme/1">Job</a>
              <a href="https://jobs.lever.co/acme/2">Job</a>
              <a href="https://boards.greenhouse.io/other">x</a></html>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=page)

    guess = detect("https://acme.example/careers", client_for(handler))
    assert guess is not None
    assert (guess.ats, guess.id) == (ATS.lever, "acme")  # most frequent wins


def test_unknown_page_returns_none():
    assert best_guess("https://www.example.com/careers") is None


def test_workday_probe_reads_one_page(load_fixture, client_for):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={**load_fixture("workday_intel_list.json"), "total": 791})

    company = Company(
        name="Intel", ats=ATS.workday, id="intel", workday_dc=1, workday_site="External"
    )
    assert get_adapter(ATS.workday, client_for(handler)).probe(company) == 791
    assert len(calls) == 1
