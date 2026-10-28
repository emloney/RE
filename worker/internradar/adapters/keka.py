"""Keka: careers pages at https://{subdomain}.keka.com/careers/

Verified against teachmint, vyaparapp, gokwik, solarsquare and exponentenergy on 2026-10-28:
GET /careers/api/jobs/default/active returns every open job as JSON, with the HTML
description, jobLocations, experience ("0-1 year") and publishedOn. Each job's page is
/careers/jobdetails/{id}. (Some portals' own pages use an equivalent
/careers/api/embedjobs/default/active/{portal id}; this endpoint needs no id.)
Keka's robots.txt disallows "/" but allows "/careers", so this is allowed
(RFC 9309: the longer, more specific rule wins).
"""

from __future__ import annotations

from typing import Any

from internradar.adapters.base import Adapter
from internradar.http import FetchError
from internradar.models import ATS, Company, Job, make_job_id
from internradar.text import html_to_text, looks_remote, parse_dt


def careers_base(company: Company) -> str:
    return f"https://{company.id}.keka.com/careers"


class KekaAdapter(Adapter):
    ats = ATS.keka

    def list_jobs(self, company: Company) -> list[Job]:
        api = f"{careers_base(company)}/api/jobs/default/active"
        if not self.http.allowed_by_robots(api):
            raise FetchError(f"robots.txt does not allow fetching {api}")
        data = self.http.get_json(api)
        if not isinstance(data, list):
            raise FetchError(f"unexpected response from {api}")
        return [self._parse(company, raw) for raw in data]

    def _parse(self, company: Company, raw: dict[str, Any]) -> Job:
        places: list[str] = []
        for loc in raw.get("jobLocations") or []:
            city = loc.get("city") or loc.get("name") or ""
            place = ", ".join(p for p in (city, loc.get("countryName") or "") if p)
            if place and place not in places:
                places.append(place)
        location = "; ".join(places)

        description = html_to_text(raw.get("description"))
        if raw.get("experience"):
            # Keka has a separate experience field; put it where the filter looks.
            description = f"Experience: {raw['experience']}\n\n{description}"

        ats_id = str(raw["id"])
        return Job(
            id=make_job_id(company.key, ats_id),
            company=company.name,
            ats_job_id=ats_id,
            title=raw["title"],
            location=location,
            is_remote=looks_remote(location, raw.get("title")),
            department=raw.get("departmentName"),
            url=f"{careers_base(company)}/jobdetails/{ats_id}",
            posted_at=parse_dt(raw.get("publishedOn")),
            description_text=description,
            source_ats=self.ats,
        )
