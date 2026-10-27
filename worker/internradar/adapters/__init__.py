"""ATS adapters. To add one: subclass Adapter, then register it in ADAPTERS."""

from __future__ import annotations

from internradar.adapters.ashby import AshbyAdapter
from internradar.adapters.base import Adapter
from internradar.adapters.greenhouse import GreenhouseAdapter
from internradar.adapters.html import HtmlAdapter
from internradar.adapters.lever import LeverAdapter
from internradar.adapters.smartrecruiters import SmartRecruitersAdapter
from internradar.adapters.workday import WorkdayAdapter
from internradar.http import PoliteClient
from internradar.models import ATS

ADAPTERS: dict[ATS, type[Adapter]] = {
    a.ats: a
    for a in (
        GreenhouseAdapter,
        LeverAdapter,
        AshbyAdapter,
        SmartRecruitersAdapter,
        WorkdayAdapter,
        HtmlAdapter,
    )
}


def get_adapter(ats: ATS, http: PoliteClient) -> Adapter:
    return ADAPTERS[ats](http)


__all__ = ["ADAPTERS", "Adapter", "get_adapter"]
