"""Keka adapter, against real jobs JSON captured from teachmint.keka.com (trimmed to 3 jobs)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from internradar.adapters import get_adapter
from internradar.config import repo_root
from internradar.detect_ats import best_guess
from internradar.filters import FilterConfig, evaluate
from internradar.http import FetchError, robots_allows
from internradar.models import ATS, Company, make_job_id

TEACHMINT = Company(name="Teachmint", ats=ATS.keka, id="teachmint")
KEKA_ROBOTS = "User-agent: *\nDisallow: /\nAllow: /careers\nAllow: /careers/\n"
API = "/careers/api/jobs/default/active"


def keka_site(load_fixture, robots=KEKA_ROBOTS, body=None):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        if request.url.path == API:
            return httpx.Response(
                200, json=load_fixture("keka_teachmint_active.json") if body is None else body
            )
        return httpx.Response(404)

    return handler, seen


def test_keka_lists_jobs_from_the_real_response(load_fixture, client_for):
    handler, seen = keka_site(load_fixture)
    jobs = get_adapter(ATS.keka, client_for(handler)).list_jobs(TEACHMINT)

    assert seen == ["/robots.txt", API]  # one request for the jobs, after robots.txt
    assert len(jobs) == 3
    job = jobs[0]
    assert job.title == "Intern — International Business"
    assert job.ats_job_id == "145903"
    assert job.id == make_job_id("keka:teachmint", "145903")
    assert job.location == "Bangalore, India"
    assert job.department == "GTM International"
    assert job.url == "https://teachmint.keka.com/careers/jobdetails/145903"
    assert job.posted_at == datetime(2026, 10, 28, 8, 52, 42, 227000, tzinfo=UTC)
    assert job.description_text.startswith("Experience: 0-1 year")
    assert "<div>" not in job.description_text
    assert not job.needs_details
    assert jobs[1].location == ""  # Keka sometimes sends jobLocations: null


def test_keka_jobs_go_through_the_normal_filter(load_fixture, client_for):
    handler, _ = keka_site(load_fixture)
    job = get_adapter(ATS.keka, client_for(handler)).list_jobs(TEACHMINT)[0]
    config = FilterConfig.load(repo_root() / "filters.yaml")
    # An intern role in Bangalore, but not a data/AI title: correctly not a match.
    assert evaluate(job, config).reason == "role: no data/AI keyword in the title"
    renamed = job.model_copy(update={"title": "Data Analyst Intern"})
    assert evaluate(renamed, config).passed is True


def test_keka_rejects_unexpected_responses(load_fixture, client_for):
    handler, _ = keka_site(load_fixture, body={"error": "not a list"})
    with pytest.raises(FetchError, match="unexpected response"):
        get_adapter(ATS.keka, client_for(handler)).list_jobs(TEACHMINT)


def test_keka_respects_robots(load_fixture, client_for):
    handler, seen = keka_site(load_fixture, robots="User-agent: *\nDisallow: /careers\n")
    with pytest.raises(FetchError, match="robots.txt"):
        get_adapter(ATS.keka, client_for(handler)).list_jobs(TEACHMINT)
    assert API not in seen  # never fetched


@pytest.mark.parametrize(
    ("robots", "path", "allowed"),
    [
        (KEKA_ROBOTS, "/careers/api/embedjobs/default/active/x", True),
        (KEKA_ROBOTS, "/careers", True),
        (KEKA_ROBOTS, "/admin", False),
        (KEKA_ROBOTS, "/", False),
        ("", "/anything", True),
        ("User-agent: *\nDisallow: /careers\n", "/careers/jobs", False),
        ("User-agent: *\nDisallow: /careers\nAllow: /careers/open\n", "/careers/open/1", True),
        ("User-agent: *\nDisallow: /\n\nUser-agent: InternRadar\nAllow: /\n", "/x", True),
        ("User-agent: googlebot\nDisallow: /\n", "/x", True),
        ("User-agent: *\nDisallow: /x\nAllow: /x\n", "/x", True),  # tie: Allow wins
        ("User-agent: *\nDisallow: /*.pdf$\n", "/doc.pdf", False),
    ],
)
def test_robots_rfc9309_longest_match(robots, path, allowed):
    assert robots_allows(robots, path) is allowed


@pytest.mark.parametrize(
    ("text", "slug"),
    [
        ("https://teachmint.keka.com/careers/", "teachmint"),
        ("https://vyaparapp.keka.com/careers/api/embedjobs/641c96f0", "vyaparapp"),
        (
            '<script src="https://cdn.keka.com/careers/v/2026/x.js"></script>'
            '<a href="https://gokwik.keka.com/careers">jobs</a>',
            "gokwik",
        ),
    ],
)
def test_detect_keka(text, slug):
    guess = best_guess(text)
    assert guess is not None and (guess.ats, guess.id) == (ATS.keka, slug)


def test_cdn_alone_is_not_a_company():
    assert best_guess("https://cdn.keka.com/careers/v/2026/") is None
