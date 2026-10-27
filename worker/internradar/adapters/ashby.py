"""Ashby: GET https://api.ashbyhq.com/posting-api/job-board/{board_name}

Returns {"jobs": [...]} with descriptions included. Unlisted jobs are skipped.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from internradar.adapters.base import Adapter
from internradar.models import ATS, Company, Job, make_job_id
from internradar.text import html_to_text, looks_remote, parse_dt

API = "https://api.ashbyhq.com/posting-api/job-board/{board}"


class AshbyAdapter(Adapter):
    ats = ATS.ashby

    def list_jobs(self, company: Company) -> list[Job]:
        data = self.http.get_json(API.format(board=quote(company.id, safe="")))
        return [
            self._parse(company, raw) for raw in data.get("jobs", []) if raw.get("isListed", True)
        ]

    def _parse(self, company: Company, raw: dict[str, Any]) -> Job:
        primary = raw.get("location") or ""
        locations = [primary] + [
            s["location"]
            for s in raw.get("secondaryLocations") or []
            if s.get("location") and s["location"] != primary
        ]
        location = "; ".join(x for x in locations if x)
        return Job(
            id=make_job_id(company.key, raw["id"]),
            company=company.name,
            ats_job_id=raw["id"],
            title=raw["title"].strip(),
            location=location,
            is_remote=bool(raw.get("isRemote"))
            or raw.get("workplaceType") == "Remote"
            or looks_remote(location),
            department=raw.get("department") or raw.get("team"),
            url=raw["jobUrl"],
            posted_at=parse_dt(raw.get("publishedAt")),
            description_text=raw.get("descriptionPlain")
            or html_to_text(raw.get("descriptionHtml")),
            source_ats=self.ats,
        )
