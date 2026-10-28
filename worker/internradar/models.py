"""Data models shared by every part of the worker."""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from internradar.text import normalise_location, normalise_title


class ATS(StrEnum):
    """Applicant Tracking Systems we have an adapter for."""

    greenhouse = "greenhouse"
    lever = "lever"
    ashby = "ashby"
    smartrecruiters = "smartrecruiters"
    workday = "workday"
    keka = "keka"
    html = "html"


class HtmlSelectors(BaseModel):
    """CSS selectors for a custom careers page (ats: html)."""

    model_config = ConfigDict(extra="forbid")

    list_url: str | None = None  # page that lists jobs; defaults to careers_url
    item: str  # one element per job
    title: str  # inside item
    link: str | None = None  # inside item; defaults to the item itself or its first <a>
    location: str | None = None  # inside item
    render_js: bool = False  # use headless Playwright (only if the page needs JavaScript)


class Company(BaseModel):
    """One entry from companies.yaml (or, later, the companies table)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    ats: ATS
    id: str  # board token / slug / tenant, depending on the ATS
    workday_dc: int | None = None  # the N in {tenant}.wd{N}.myworkdayjobs.com
    workday_site: str | None = None
    workday_search: str | None = None  # optional searchText to limit Workday results
    careers_url: str | None = None  # human link, for reference
    html_selectors: HtmlSelectors | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def _check_ats_fields(self) -> Company:
        if self.ats is ATS.workday and (not self.workday_site or not self.workday_dc):
            raise ValueError("workday companies need workday_dc and workday_site")
        if self.ats is ATS.html:
            if self.html_selectors is None:
                raise ValueError("html companies need html_selectors")
            if not (self.html_selectors.list_url or self.careers_url):
                raise ValueError("html companies need careers_url or html_selectors.list_url")
        return self

    @property
    def key(self) -> str:
        """Stable identity used in job ids. Renaming a company does not change it."""
        return f"{self.ats.value}:{self.id}".lower()


def make_job_id(
    company_key: str, ats_job_id: str | None, title: str = "", location: str = ""
) -> str:
    """Stable job id: hash of company + ATS job id, or of company + title + location."""
    if ats_job_id:
        raw = f"{company_key}|id|{ats_job_id}"
    else:
        raw = f"{company_key}|tl|{normalise_title(title)}|{normalise_location(location)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class Job(BaseModel):
    """A job posting, normalised so every adapter returns the same shape."""

    id: str
    company: str
    ats_job_id: str | None
    title: str
    location: str = ""
    is_remote: bool = False
    department: str | None = None
    url: str  # direct apply / posting link
    posted_at: datetime | None = None  # from the ATS, when it gives one
    first_seen_at: datetime | None = None  # set when OUR bot first sees it (Phase 2)
    description_text: str = ""
    source_ats: ATS
    # True when the list response lacked the description (and maybe the date or
    # full location). The adapter's fill_details() fetches them on demand.
    needs_details: bool = False

    @field_validator("title", "location")
    @classmethod
    def _strip(cls, value: str) -> str:
        return " ".join(value.split())
