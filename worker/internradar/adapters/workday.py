"""Workday: POST https://{tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs

Body: {"limit": 20, "offset": 0, "searchText": "", "appliedFacets": {}}. Paginate with offset.
Quirks (seen in real responses):
- "total" is only filled in on the first page; later pages return total = 0.
- The list only has title, externalPath, locationsText and a relative "postedOn"
  ("Posted 30+ Days Ago"). The description and real date (startDate) come from
  GET .../wday/cxs/{tenant}/{site}{externalPath}.
- locationsText can be a summary like "2 Locations"; the detail call has the real ones.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from internradar.adapters.base import Adapter
from internradar.http import FetchError
from internradar.models import ATS, Company, Job, make_job_id
from internradar.text import html_to_text, looks_remote, parse_dt

log = logging.getLogger(__name__)

PAGE_SIZE = 20  # Workday rejects larger pages
MAX_PAGES = 50
MULTI_LOCATION = re.compile(r"^\d+ Locations?$", re.IGNORECASE)


def _base(company: Company) -> str:
    return f"https://{company.id}.wd{company.workday_dc}.myworkdayjobs.com"


def _cxs(company: Company) -> str:
    return f"{_base(company)}/wday/cxs/{company.id}/{company.workday_site}"


def _public_prefix(company: Company) -> str:
    return f"{_base(company)}/{company.workday_site}"


class WorkdayAdapter(Adapter):
    ats = ATS.workday

    def _page(self, company: Company, offset: int) -> dict[str, Any]:
        body = {
            "limit": PAGE_SIZE,
            "offset": offset,
            "searchText": company.workday_search or "",
            "appliedFacets": {},
        }
        return self.http.post_json(f"{_cxs(company)}/jobs", body)

    def probe(self, company: Company) -> int:
        return int(self._page(company, 0).get("total") or 0)

    def list_jobs(self, company: Company) -> list[Job]:
        jobs: list[Job] = []
        total: int | None = None
        offset = 0
        for _ in range(MAX_PAGES):
            data = self._page(company, offset)
            if total is None:
                total = int(data.get("total") or 0)
            postings = data.get("jobPostings") or []
            jobs.extend(self._parse(company, raw) for raw in postings if raw.get("externalPath"))
            offset += len(postings)
            if not postings or offset >= total:
                return jobs
        # Fail instead of returning a cut-off list: otherwise every job past the
        # cap would look "missing" and eventually get marked closed.
        raise FetchError(
            f"more than {MAX_PAGES * PAGE_SIZE} jobs; set workday_search to narrow it down"
        )

    def _parse(self, company: Company, raw: dict[str, Any]) -> Job:
        path = raw["externalPath"]  # e.g. /job/India-Bangalore/Accountant_JR0283198
        # The last path segment is unique per posting; the requisition id in
        # bulletFields can be shared by reposts (..._JR0285850-1).
        ats_id = path.rstrip("/").rsplit("/", 1)[-1]
        location = raw.get("locationsText") or ""
        return Job(
            id=make_job_id(company.key, ats_id),
            company=company.name,
            ats_job_id=ats_id,
            title=raw["title"].strip(),
            location="" if MULTI_LOCATION.match(location) else location,
            is_remote=looks_remote(location),
            url=f"{_public_prefix(company)}{path}",
            source_ats=self.ats,
            needs_details=True,
        )

    def fill_details(self, company: Company, job: Job) -> Job:
        path = job.url.removeprefix(_public_prefix(company))
        info = self.http.get_json(f"{_cxs(company)}{path}").get("jobPostingInfo") or {}
        locations = [info.get("location") or ""] + list(info.get("additionalLocations") or [])
        location = "; ".join(x for x in locations if x) or job.location
        return job.model_copy(
            update={
                "location": location,
                "is_remote": job.is_remote or looks_remote(location, info.get("remoteType")),
                "posted_at": parse_dt(info.get("startDate")) or job.posted_at,
                "description_text": html_to_text(info.get("jobDescription")),
                "url": info.get("externalUrl") or job.url,
                "needs_details": False,
            }
        )
