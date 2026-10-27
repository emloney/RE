"""Lever: GET https://api.lever.co/v0/postings/{company}?mode=json

Returns a JSON list with plain-text descriptions included.
Note: companies on Lever's EU instance (jobs.eu.lever.co) are not supported yet.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from internradar.adapters.base import Adapter
from internradar.models import ATS, Company, Job, make_job_id
from internradar.text import html_to_text, looks_remote, parse_dt

API = "https://api.lever.co/v0/postings/{slug}?mode=json"


class LeverAdapter(Adapter):
    ats = ATS.lever

    def list_jobs(self, company: Company) -> list[Job]:
        data = self.http.get_json(API.format(slug=quote(company.id, safe="")))
        return [self._parse(company, raw) for raw in data]

    def _parse(self, company: Company, raw: dict[str, Any]) -> Job:
        categories = raw.get("categories") or {}
        primary = categories.get("location") or ""
        locations = [primary] + [x for x in categories.get("allLocations") or [] if x != primary]
        location = "; ".join(x for x in locations if x)

        parts = [raw.get("descriptionPlain") or ""]
        for block in raw.get("lists") or []:
            parts.append(block.get("text") or "")
            parts.append(html_to_text(block.get("content")))
        parts.append(raw.get("additionalPlain") or "")

        return Job(
            id=make_job_id(company.key, raw["id"]),
            company=company.name,
            ats_job_id=raw["id"],
            title=raw["text"].strip(),
            location=location,
            is_remote=raw.get("workplaceType") == "remote" or looks_remote(location),
            department=categories.get("department") or categories.get("team"),
            url=raw["hostedUrl"],
            posted_at=parse_dt(raw.get("createdAt")),  # epoch milliseconds
            description_text="\n\n".join(p.strip() for p in parts if p and p.strip()),
            source_ats=self.ats,
        )
