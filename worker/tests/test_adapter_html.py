"""Generic HTML adapter: CSS selectors + robots.txt."""

from __future__ import annotations

import httpx
import pytest

from internradar.adapters import get_adapter
from internradar.http import FetchError
from internradar.models import ATS, Company, make_job_id

from .conftest import FIXTURES

PAGE = (FIXTURES / "html_careers_page.html").read_text(encoding="utf-8")
COMPANY = Company(
    name="Example Co",
    ats=ATS.html,
    id="exampleco",
    careers_url="https://example.com/careers",
    html_selectors={
        "item": "li.job",
        "title": ".job-title",
        "link": "a.job-link",
        "location": ".job-location",
    },
)


def _site(robots: httpx.Response):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return robots
        if request.url.path == "/careers":
            return httpx.Response(200, text=PAGE)
        return httpx.Response(404)

    return handler


def test_html_adapter_parses_jobs(client_for):
    http = client_for(_site(httpx.Response(404)))  # no robots.txt => allowed
    jobs = get_adapter(ATS.html, http).list_jobs(COMPANY)

    assert [j.title for j in jobs] == ["Data Analyst Intern", "Machine Learning Engineer"]
    first, second = jobs
    assert first.url == "https://example.com/careers/data-analyst-intern"  # relative link resolved
    assert first.location == "Bengaluru, India"
    assert first.ats_job_id is None
    assert first.id == make_job_id(
        "html:exampleco", None, "Data Analyst Intern", "Bengaluru, India"
    )
    assert second.is_remote


def test_html_adapter_respects_robots(client_for):
    robots = httpx.Response(200, text="User-agent: *\nDisallow: /careers\n")
    with pytest.raises(FetchError, match="robots.txt"):
        get_adapter(ATS.html, client_for(_site(robots))).list_jobs(COMPANY)


def test_html_adapter_skips_when_robots_unreachable(client_for):
    with pytest.raises(FetchError, match="robots.txt"):
        get_adapter(ATS.html, client_for(_site(httpx.Response(503)))).list_jobs(COMPANY)
