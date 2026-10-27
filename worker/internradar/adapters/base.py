"""The adapter interface. One adapter per ATS; all return normalised Job objects."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from internradar.http import PoliteClient
from internradar.models import ATS, Company, Job


class Adapter(ABC):
    ats: ClassVar[ATS]

    def __init__(self, http: PoliteClient) -> None:
        self.http = http

    @abstractmethod
    def list_jobs(self, company: Company) -> list[Job]:
        """Return every job currently open at this company."""

    def probe(self, company: Company) -> int:
        """Cheap check that the board exists: number of open jobs.

        Paginated adapters override this to read only the first page.
        """
        return len(self.list_jobs(company))

    def fill_details(self, company: Company, job: Job) -> Job:
        """Fetch what the list call left out (description, date). Default: nothing to do."""
        return job
