"""Generic fallback for custom careers pages, using CSS selectors from companies.yaml.

Checks robots.txt first. Uses headless Playwright only when render_js is true,
and only for that company (pip install "internradar[js]" && playwright install chromium).
Jobs here have no ATS id, so the id is a hash of company + title + location.
"""

from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from internradar.adapters.base import Adapter
from internradar.http import FetchError
from internradar.models import ATS, Company, Job, make_job_id
from internradar.text import looks_remote


class HtmlAdapter(Adapter):
    ats = ATS.html

    def list_jobs(self, company: Company) -> list[Job]:
        selectors = company.html_selectors
        assert selectors is not None  # guaranteed by Company validation
        url = selectors.list_url or company.careers_url or ""
        if not self.http.allowed_by_robots(url):
            raise FetchError(f"robots.txt does not allow fetching {url}")
        page = self._render(url) if selectors.render_js else self.http.get_text(url)

        soup = BeautifulSoup(page, "html.parser")
        jobs: list[Job] = []
        for item in soup.select(selectors.item):
            title_el = item.select_one(selectors.title)
            if title_el is None:
                continue
            title = title_el.get_text(" ", strip=True)
            if selectors.link:
                link_el = item.select_one(selectors.link)
            else:
                link_el = item if item.name == "a" else item.find("a")
            href = link_el.get("href") if link_el is not None else None
            location = ""
            if selectors.location:
                loc_el = item.select_one(selectors.location)
                location = loc_el.get_text(" ", strip=True) if loc_el else ""
            jobs.append(
                Job(
                    id=make_job_id(company.key, None, title, location),
                    company=company.name,
                    ats_job_id=None,
                    title=title,
                    location=location,
                    is_remote=looks_remote(location),
                    url=urljoin(url, str(href)) if href else url,
                    source_ats=self.ats,
                )
            )
        return jobs

    def _render(self, url: str) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise FetchError(
                'render_js needs Playwright: pip install "internradar[js]" '
                "&& playwright install chromium"
            ) from exc
        self.http.throttle(url)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(user_agent=self.http.user_agent)
                page.goto(url, timeout=15_000, wait_until="networkidle")
                return page.content()
            finally:
                browser.close()
