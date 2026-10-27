"""SmartRecruiters: GET https://api.smartrecruiters.com/v1/companies/{company_id}/postings

The list is paginated (offset/limit, totalFound) and has no description.
The description comes from GET .../postings/{id} under jobAd.sections.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from internradar.adapters.base import Adapter
from internradar.models import ATS, Company, Job, make_job_id
from internradar.text import clean_location, html_to_text, looks_remote, parse_dt

log = logging.getLogger(__name__)

API = "https://api.smartrecruiters.com/v1/companies/{company}/postings"
PAGE_SIZE = 100
MAX_PAGES = 50  # 5,000 jobs: plenty, and stops a runaway loop
# Sections worth sending to the LLM. "companyDescription" is boilerplate, so skip it.
DETAIL_SECTIONS = ("jobDescription", "qualifications", "additionalInformation")


class SmartRecruitersAdapter(Adapter):
    ats = ATS.smartrecruiters

    def _base(self, company: Company) -> str:
        return API.format(company=quote(company.id, safe=""))

    def probe(self, company: Company) -> int:
        data = self.http.get_json(self._base(company), params={"limit": 1, "offset": 0})
        return int(data.get("totalFound") or 0)

    def list_jobs(self, company: Company) -> list[Job]:
        jobs: list[Job] = []
        offset = 0
        for _ in range(MAX_PAGES):
            data = self.http.get_json(
                self._base(company), params={"limit": PAGE_SIZE, "offset": offset}
            )
            content = data.get("content") or []
            jobs.extend(self._parse(company, raw) for raw in content)
            offset += len(content)
            if not content or offset >= int(data.get("totalFound") or 0):
                return jobs
        log.warning("%s: stopped after %d pages", company.name, MAX_PAGES)
        return jobs

    def _parse(self, company: Company, raw: dict[str, Any]) -> Job:
        loc = raw.get("location") or {}
        location = clean_location(loc.get("fullLocation") or loc.get("city") or "")
        ats_id = str(raw["id"])
        return Job(
            id=make_job_id(company.key, ats_id),
            company=company.name,
            ats_job_id=ats_id,
            title=raw["name"].strip(),
            location=location,
            is_remote=bool(loc.get("remote")) or looks_remote(location),
            department=(raw.get("department") or {}).get("label"),
            # Verified: this short form resolves to the posting page.
            url=f"https://jobs.smartrecruiters.com/{company.id}/{ats_id}",
            posted_at=parse_dt(raw.get("releasedDate")),
            source_ats=self.ats,
            needs_details=True,
        )

    def fill_details(self, company: Company, job: Job) -> Job:
        data = self.http.get_json(f"{self._base(company)}/{quote(job.ats_job_id or '', safe='')}")
        sections = (data.get("jobAd") or {}).get("sections") or {}
        parts = []
        for name in DETAIL_SECTIONS:
            section = sections.get(name) or {}
            body = html_to_text(section.get("text"))
            if body:
                parts.append(f"{section.get('title') or name}\n{body}")
        return job.model_copy(
            update={
                "description_text": "\n\n".join(parts),
                "url": data.get("postingUrl") or job.url,
                "needs_details": False,
            }
        )
