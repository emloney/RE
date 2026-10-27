"""Rule filter, using the real filters.yaml from the repo root."""

from __future__ import annotations

import pytest

from internradar.config import repo_root
from internradar.filters import FilterConfig, evaluate, location_ok, min_years_asked
from internradar.models import ATS, Job

CONFIG = FilterConfig.load(repo_root() / "filters.yaml")


def job(title, location="Bengaluru, India", description="Great internship.", **kw) -> Job:
    return Job(
        id="x",
        company="Acme",
        ats_job_id="1",
        title=title,
        location=location,
        url="https://x",
        description_text=description,
        source_ats=kw.pop("source_ats", ATS.lever),
        **kw,
    )


@pytest.mark.parametrize(
    ("title", "category"),
    [
        ("Data Analyst Intern", "data"),
        ("Business Analyst - Internship (6 months)", "data"),
        ("Machine Learning Intern", "aiml"),
        ("GenAI Engineer - Fresher", "aiml"),
        ("ML Research Intern", "aiml"),
        ("Graduate Trainee - Analytics", "data"),
    ],
)
def test_matching_titles_pass(title, category):
    result = evaluate(job(title), CONFIG)
    assert result.passed is True, result.reason
    assert result.category == category


@pytest.mark.parametrize(
    ("title", "reason"),
    [
        ("Senior Data Analyst", "level: title says 'senior'"),
        ("Sr. ML Engineer", "level: title says 'sr'"),
        ("Lead - Analytics", "level: title says 'lead'"),
        ("AM/ Manager - Risk Analytics", "level: title says 'manager'"),
        ("Assistant Manager - Internal Audit", "role: no data/AI keyword in the title"),
        ("Video Editor Intern", "role: no data/AI keyword in the title"),
        ("Email Marketing Intern", "role: no data/AI keyword in the title"),  # not "ai"
        ("HTML Developer Intern", "role: no data/AI keyword in the title"),  # not "ml"
    ],
)
def test_ruled_out_titles_say_why(title, reason):
    result = evaluate(job(title), CONFIG)
    assert result.passed is False
    assert result.reason == reason


@pytest.mark.parametrize(
    ("location", "ok"),
    [
        ("Bengaluru-VTP, India", True),
        ("Bangalore, Karnataka", True),
        ("BLR", True),
        ("Austin; New York City; Bengaluru", True),
        ("Remote - India", True),
        ("India - Remote", True),
        ("Remote", True),
        ("Remote - US", False),
        ("Remote, EMEA", False),
        ("Austin; New York City; San Francisco", False),
        ("Hyderabad", False),
    ],
)
def test_locations(location, ok):
    assert location_ok(location, CONFIG)[0] is ok


def test_experience_and_level_from_description():
    passed = evaluate(job("AI Builder", description="Needs 0-1 years of experience."), CONFIG)
    assert passed.passed is True
    too_senior = evaluate(job("Data Scientist", description="3+ years of experience."), CONFIG)
    assert too_senior.reason == "experience: asks for 3+ years"
    no_signal = evaluate(job("Data Scientist", description="Join our team."), CONFIG)
    assert no_signal.reason == "level: no intern/fresher/entry-level signal"


def test_phd_required_is_excluded():
    result = evaluate(job("ML Research Intern", description="A PhD is required."), CONFIG)
    assert result.reason == "level: PhD required"


def test_missing_description_or_location_means_fetch_details_first():
    workday = job(
        "Data Scientist II",
        location="",
        description="",
        source_ats=ATS.workday,
        needs_details=True,
    )
    assert evaluate(workday, CONFIG).passed is None
    intern = job("Data Analyst Intern", location="", description="", needs_details=True)
    assert evaluate(intern, CONFIG).passed is None  # location unknown until details


def test_wrong_location_is_decided_without_details():
    result = evaluate(job("Data Analyst Intern", location="Austin", description=""), CONFIG)
    assert result.passed is False and result.reason.startswith("location:")


@pytest.mark.parametrize(
    ("text", "years"),
    [
        ("Minimum 3 years of experience in SQL", 3),
        ("3-5 yrs experience", 3),
        ("5+ years’ experience with Python", 5),
        ("experience: 4+ years", 4),
        ("We were founded 10 years ago.", None),
        ("0-1 years of experience; 2+ years experience preferred", 0),
    ],
)
def test_min_years_asked(text, years):
    assert min_years_asked(text) == years


def test_level_match_can_be_turned_off():
    loose = CONFIG.model_copy(update={"require_level_match": False})
    assert evaluate(job("Data Scientist", description="Join our team."), loose).passed is True
