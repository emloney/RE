"""Filter and score new jobs: rule filter -> (details if needed) -> LLM score.

Only genuinely new jobs get here. Every outcome is saved on the job row, so a
job is never scored twice and a crash mid-run is picked up on the next run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from internradar.fetch import fill_details
from internradar.filters import FilterConfig, FilterResult, evaluate
from internradar.http import PoliteClient
from internradar.llm import LLM
from internradar.models import Company, Job
from internradar.scorer import ScoreResult, score_job
from internradar.store import Store, evaluation_row

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Evaluated:
    company: Company
    job: Job
    filter: FilterResult
    score: ScoreResult | None = None
    llm_calls: int = 0

    @property
    def matched(self) -> bool:
        return bool(self.filter.passed)


@dataclass
class Evaluator:
    filters: FilterConfig
    llm: LLM | None
    resume: str | None
    # Why scoring is off, stored on the job so you can see it in the dashboard.
    no_llm_reason: str = ""

    def evaluate(self, company: Company, job: Job, http: PoliteClient) -> Evaluated:
        result = evaluate(job, self.filters)
        # Still in the running and the list call had no description: fetch details and
        # look again (the description can rule it out, and the LLM needs it anyway).
        if result.passed is not False and job.needs_details:
            job = fill_details(company, job, http)
            result = evaluate(job, self.filters)
            if result.passed is None:  # still unknown (details failed): let it through
                result = FilterResult(
                    True, f"could not check fully: {result.reason}", result.category
                )
        if not result.passed:
            log.info("filtered out: %s — %s: %s", company.name, job.title, result.reason)
            return Evaluated(company, job, result)

        if self.llm is None or self.resume is None:
            return Evaluated(company, job, result)
        scored = score_job(self.llm, self.resume, job)
        return Evaluated(company, job, result, scored.result, scored.calls)

    def fields(self, ev: Evaluated, now: datetime) -> dict[str, Any]:
        """What to save on the job row."""
        fields = {**evaluation_row(ev.job), **ev.filter.as_row()}
        if ev.matched:
            fields["scored_at"] = now.isoformat()  # the cache: never scored twice
            if self.llm is not None and self.resume is not None:
                fields["llm_model"] = self.llm.name
            else:
                fields["llm_model"] = f"not scored: {self.no_llm_reason}"[:200]
            if ev.score is not None:
                fields["score"] = ev.score.score
                fields["score_why"] = ev.score.why
                fields["missing_skills"] = ev.score.missing_skills
        return fields


def evaluate_and_save(
    evaluator: Evaluator,
    store: Store,
    candidates: list[tuple[Company, Job]],
    http: PoliteClient,
    now: datetime,
) -> list[Evaluated]:
    results = []
    for company, job in candidates:
        ev = evaluator.evaluate(company, job, http)
        store.save_evaluation(job.id, evaluator.fields(ev, now))
        results.append(ev)
    return results


def sort_matches(results: list[Evaluated]) -> list[Evaluated]:
    """Matched jobs, best score first, unscored last."""
    matched = [r for r in results if r.matched]
    return sorted(matched, key=lambda r: (r.score is None, -(r.score.score if r.score else 0)))
