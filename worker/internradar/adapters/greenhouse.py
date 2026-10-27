"""Greenhouse: GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true

One call returns every job with its description (as double-escaped HTML).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from internradar.adapters.base import Adapter
from internradar.models import ATS, Company, Job, make_job_id
from internradar.text import html_to_text, looks_remote, parse_dt

API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


class GreenhouseAdapter(Adapter):
    ats = ATS.greenhouse

    def list_jobs(self, company: Company) -> list[Job]:
        data = self.http.get_json(API.format(token=quote(company.id, safe="")))
        return [self._parse(company, raw) for raw in data.get("jobs", [])]

    def _parse(self, company: Company, raw: dict[str, Any]) -> Job:
        ats_id = str(raw["id"])
        location = (raw.get("location") or {}).get("name") or ""
        departments = [d["name"] for d in raw.get("departments") or [] if d.get("name")]
        return Job(
            id=make_job_id(company.key, ats_id),
            company=company.name,
            ats_job_id=ats_id,
            title=raw["title"].strip(),
            location=location,
            is_remote=looks_remote(location),
            department=", ".join(departments) or None,
            url=raw["absolute_url"],  # may be on job-boards.eu.greenhouse.io: never rebuild it
            posted_at=parse_dt(raw.get("first_published") or raw.get("updated_at")),
            description_text=html_to_text(raw.get("content")),
            source_ats=self.ats,
        )
