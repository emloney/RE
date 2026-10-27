"""Rule-based job filter. Runs before the LLM and records WHY each job was
kept or dropped, so you can debug your filters.

evaluate() returns one of three outcomes:
- passed = True:  role, level and location all fit
- passed = False: ruled out (reason says which rule)
- passed = None:  can't tell yet. The list call had no description or location,
                  so fetch the details and evaluate again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from internradar.models import Job

# "3+ years of experience", "3-5 yrs experience", "minimum 4 years' experience"
_YEARS_THEN_EXP = re.compile(
    r"(\d{1,2})\s*(?:\+|plus|-\s*\d{1,2}|to\s*\d{1,2})?\s*(?:years?|yrs?)['â€™]?"
    r"(?:\s+of)?[^.\n]{0,30}?\bexperience",
    re.IGNORECASE,
)
# "experience of 3+ years", "experience: 5-7 years"
_EXP_THEN_YEARS = re.compile(
    r"\bexperience\b[^.\n]{0,25}?(\d{1,2})\s*(?:\+|plus|-\s*\d{1,2}|to\s*\d{1,2})?\s*(?:years?|yrs?)\b",
    re.IGNORECASE,
)
_PHD_REQUIRED = re.compile(
    r"\bph\.?\s?d\.?\b[^.\n]{0,40}\b(?:required|mandatory|must)\b"
    r"|\b(?:required|must have)\b[^.\n]{0,40}\bph\.?\s?d\b",
    re.IGNORECASE,
)
_INDIA = re.compile(r"\bindia\b|\bind\b", re.IGNORECASE)
_REMOTE = re.compile(r"\bremote\b|work from home|\bwfh\b|\banywhere\b", re.IGNORECASE)
# Words that, besides "remote", carry no country information.
_REMOTE_FILLER = {"remote", "anywhere", "work", "from", "home", "wfh", "fully", "first", "only"}


class FilterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roles: dict[str, list[str]] = Field(default_factory=dict)
    levels: list[str] = Field(default_factory=list)
    require_level_match: bool = True
    exclude_titles: list[str] = Field(default_factory=list)
    max_experience_years: int | None = 2
    exclude_phd_required: bool = True
    locations: list[str] = Field(default_factory=list)
    remote_ok: bool = True

    @classmethod
    def load(cls, path: Path) -> FilterConfig:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    @cached_property
    def role_patterns(self) -> list[tuple[str, str, re.Pattern[str]]]:
        return [(cat, kw, _word(kw)) for cat, kws in self.roles.items() for kw in kws]

    @cached_property
    def level_patterns(self) -> list[tuple[str, re.Pattern[str]]]:
        return [(kw, _word(kw)) for kw in self.levels]

    @cached_property
    def exclude_patterns(self) -> list[tuple[str, re.Pattern[str]]]:
        return [(kw, _word(kw)) for kw in self.exclude_titles]

    @cached_property
    def location_patterns(self) -> list[tuple[str, re.Pattern[str]]]:
        return [(kw, _word(kw)) for kw in self.locations]


def _word(keyword: str) -> re.Pattern[str]:
    """Whole-word, case-insensitive; spaces/hyphens in the keyword match either."""
    parts = [re.escape(p) for p in re.split(r"[\s-]+", keyword.strip()) if p]
    return re.compile(r"(?<![a-z0-9])" + r"[\s-]+".join(parts) + r"(?![a-z0-9])", re.IGNORECASE)


@dataclass(frozen=True)
class FilterResult:
    passed: bool | None
    reason: str
    category: str | None = None

    def as_row(self) -> dict[str, Any]:
        return {
            "filter_passed": self.passed,
            "filter_reason": self.reason,
            "category": self.category,
        }


def min_years_asked(text: str) -> int | None:
    """Smallest 'N years of experience' the description asks for, if any."""
    found = [int(m.group(1)) for p in (_YEARS_THEN_EXP, _EXP_THEN_YEARS) for m in p.finditer(text)]
    found = [n for n in found if n <= 30]
    return min(found) if found else None


def location_ok(location: str, config: FilterConfig) -> tuple[bool, str]:
    for kw, pattern in config.location_patterns:
        if pattern.search(location):
            return True, kw
    if config.remote_ok and _REMOTE.search(location):
        if _INDIA.search(location):
            return True, "remote (India)"
        words = set(re.findall(r"[a-z]+", location.lower()))
        if words <= _REMOTE_FILLER:
            return True, "remote"
        return False, f"remote, but not India-eligible ({location!r})"
    return False, f"{location!r} is not one of your locations"


def evaluate(job: Job, config: FilterConfig) -> FilterResult:
    title = job.title
    description = job.description_text
    has_details = bool(description) and not job.needs_details

    role = next(((cat, kw) for cat, kw, p in config.role_patterns if p.search(title)), None)
    if role is None:
        return FilterResult(False, "role: no data/AI keyword in the title")
    category, role_kw = role

    excluded = next((kw for kw, p in config.exclude_patterns if p.search(title)), None)
    if excluded:
        return FilterResult(False, f"level: title says {excluded!r}", category)

    where = "unknown"
    if job.location:
        ok, where = location_ok(job.location, config)
        if not ok:
            return FilterResult(False, f"location: {where}", category)

    years = min_years_asked(description) if description else None
    limit = config.max_experience_years
    if years is not None and limit is not None and years > limit:
        return FilterResult(False, f"experience: asks for {years}+ years", category)
    if config.exclude_phd_required and description and _PHD_REQUIRED.search(description):
        return FilterResult(False, "level: PhD required", category)

    level = next((kw for kw, p in config.level_patterns if p.search(title)), None)
    if level is None and description:
        level = next((kw for kw, p in config.level_patterns if p.search(description)), None)
    if level is None and years is not None and years <= 1:
        level = f"{years} years experience"
    if level is None and config.require_level_match:
        if not has_details:
            return FilterResult(None, "needs the description to check the level", category)
        return FilterResult(False, "level: no intern/fresher/entry-level signal", category)

    if not job.location and not has_details:
        return FilterResult(None, "needs details to check the location", category)

    return FilterResult(
        True,
        f"role {role_kw!r}, level {level or 'not stated'!r}, location {where!r}",
        category,
    )
