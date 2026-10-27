"""Text helpers, job ids, config loading and failure isolation in fetch_all."""

from __future__ import annotations

import httpx
import pytest

from internradar.config import ConfigError, load_companies
from internradar.fetch import fetch_all
from internradar.models import ATS, Company, make_job_id
from internradar.text import html_to_text, normalise_title, parse_dt


def test_html_to_text_handles_double_escaped_html():
    assert html_to_text("&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;") == "Hello & welcome"
    assert html_to_text("<p>Line one<br>Line two</p>") == "Line one\nLine two"
    assert html_to_text("<p>Use <b>Python</b> daily</p><p>Next</p>") == "Use Python daily\nNext"
    assert html_to_text(None) == ""


def test_normalise_title():
    assert normalise_title("  Data Analyst – Intern (2026) ") == "data analyst intern 2026"


def test_parse_dt_formats():
    assert parse_dt("2026-09-18").isoformat() == "2026-09-18T00:00:00+00:00"
    assert parse_dt("2026-10-26T06:47:08.354Z").tzinfo is not None
    assert parse_dt(1757916149833).year == 2025
    assert parse_dt("Posted 30+ Days Ago") is None


def test_job_id_is_stable_and_falls_back_to_title_location():
    assert make_job_id("lever:x", "abc") == make_job_id("lever:x", "abc")
    assert make_job_id("lever:x", "abc") != make_job_id("lever:y", "abc")
    # Without an ATS id, cosmetic title differences don't change the id.
    assert make_job_id("html:x", None, "Data Analyst - Intern", "Bengaluru") == make_job_id(
        "html:x", None, "data analyst intern", "bengaluru"
    )


def test_company_validation():
    with pytest.raises(ValueError, match="workday_dc"):
        Company(name="W", ats=ATS.workday, id="w")
    with pytest.raises(ValueError, match="html_selectors"):
        Company(name="H", ats=ATS.html, id="h", careers_url="https://h.example")


def test_load_companies_reports_every_problem(tmp_path):
    path = tmp_path / "companies.yaml"
    path.write_text(
        "- {name: A, ats: lever, id: a}\n"
        "- {name: A, ats: lever, id: a2}\n"
        "- {name: B, ats: nosuchats, id: b}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as info:
        load_companies(path)
    assert "duplicate company name: a" in str(info.value)
    assert "B:" in str(info.value)


def test_seed_companies_yaml_is_valid():
    companies = load_companies()
    assert len(companies) >= 15
    assert {c.ats for c in companies} >= {
        ATS.greenhouse,
        ATS.lever,
        ATS.ashby,
        ATS.smartrecruiters,
        ATS.workday,
    }


def test_one_failing_company_does_not_stop_the_rest(client_for):
    def handler(request: httpx.Request) -> httpx.Response:
        if "broken" in request.url.path:
            return httpx.Response(500)
        return httpx.Response(200, json=[])

    companies = [
        Company(name="Broken", ats=ATS.lever, id="broken"),
        Company(name="Fine", ats=ATS.lever, id="fine"),
        Company(name="Off", ats=ATS.lever, id="off", enabled=False),
    ]
    results = fetch_all(companies, client_for(handler))
    assert [(r.company.name, r.ok) for r in results] == [("Broken", False), ("Fine", True)]
    assert "500" in (results[0].error or "")
