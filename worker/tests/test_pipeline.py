"""Filter + details + scoring inside real runs (MemoryStore, fake ATS, fake LLM)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import httpx

from internradar.config import repo_root
from internradar.evaluate import Evaluator, sort_matches
from internradar.filters import FilterConfig
from internradar.models import ATS, Company
from internradar.run import run_once
from internradar.store import MemoryStore, ReadOnlyStore

from .test_scorer import GOOD, FakeLLM

CONFIG = FilterConfig.load(repo_root() / "filters.yaml")
ACME = Company(name="Acme", ats=ATS.lever, id="acme")


def posting(pid, title, location="Bengaluru", desc="Internship for students."):
    return {
        "id": pid,
        "text": title,
        "categories": {"location": location},
        "hostedUrl": f"https://jobs.lever.co/acme/{pid}",
        "createdAt": 1790000000000,
        "descriptionPlain": desc,
    }


class Env:
    def __init__(self, client_for, llm=None, resume="My resume"):
        self.board: list[dict] = []
        self.store = MemoryStore()
        self.http = client_for(lambda r: httpx.Response(200, json=self.board))
        self.llm = llm
        self.evaluator = Evaluator(CONFIG, llm, resume if llm else None, "no LLM in this test")
        self.t = datetime(2026, 10, 27, 2, 30, tzinfo=UTC)

    def run(self, store=None, evaluator="default"):
        self.t += timedelta(hours=4)
        ev = self.evaluator if evaluator == "default" else evaluator
        return run_once(store or self.store, [ACME], self.http, evaluator=ev, now=lambda: self.t)

    def job(self, title):
        return next(j for j in self.store.jobs.values() if j["title"] == title)


def test_new_matching_job_is_scored_and_others_explain_why(client_for):
    env = Env(client_for, FakeLLM(GOOD))
    env.board = [posting("1", "Senior Data Analyst"), posting("2", "Data Analyst Intern")]
    env.run()  # baseline
    assert env.job("Data Analyst Intern")["filter_passed"] is True  # baseline jobs get filtered
    assert env.job("Senior Data Analyst")["filter_reason"] == "level: title says 'senior'"
    assert env.job("Data Analyst Intern")["scored_at"] is None  # but never scored

    env.board += [posting("3", "ML Intern"), posting("4", "Video Editor Intern")]
    result = env.run()
    assert [e.job.title for e in sort_matches(result.evaluated)] == ["ML Intern"]
    ml = env.job("ML Intern")
    assert (ml["score"], ml["score_why"], ml["llm_model"]) == (
        8,
        "Strong Python and ML projects",
        "fake:model",
    )
    assert ml["category"] == "aiml" and ml["scored_at"] is not None
    video = env.job("Video Editor Intern")
    assert video["filter_passed"] is False and video.get("score") is None
    assert result.summary.llm_calls == 1


def test_jobs_are_never_scored_twice(client_for):
    llm = FakeLLM(GOOD)
    env = Env(client_for, llm)
    env.run()
    env.board = [posting("1", "ML Intern")]
    env.run()
    again = env.run()
    assert again.summary.llm_calls == 0 and len(llm.prompts) == 1


def test_llm_failure_keeps_the_job(client_for):
    env = Env(client_for, FakeLLM("garbage", "more garbage"))
    env.run()
    env.board = [posting("1", "ML Intern")]
    result = env.run()
    (match,) = sort_matches(result.evaluated)
    assert match.score is None  # still a match, still alerted later
    row = env.job("ML Intern")
    assert row["scored_at"] is not None and row.get("score") is None
    assert result.summary.llm_calls == 2


def test_no_llm_configured_still_filters(client_for):
    env = Env(client_for, llm=None)
    env.run()
    env.board = [posting("1", "ML Intern")]
    result = env.run()
    assert len(sort_matches(result.evaluated)) == 1
    assert env.job("ML Intern")["llm_model"] == "not scored: no LLM in this test"


def test_unfinished_jobs_from_an_earlier_run_are_picked_up(client_for):
    env = Env(client_for, FakeLLM(GOOD))
    env.run()
    env.board = [posting("1", "ML Intern")]
    env.run(evaluator=None)  # e.g. crashed before scoring
    assert env.job("ML Intern")["scored_at"] is None
    result = env.run()  # nothing new on the board, but the leftover gets scored
    assert [e.job.title for e in result.evaluated] == ["ML Intern"]
    assert env.job("ML Intern")["score"] == 8


def test_dry_run_scores_but_saves_nothing(client_for):
    env = Env(client_for, FakeLLM(GOOD))
    env.run()
    env.board = [posting("1", "ML Intern")]
    result = env.run(store=ReadOnlyStore(env.store))
    assert sort_matches(result.evaluated)[0].score.score == 8
    assert env.store.jobs == {}


def test_details_fetched_only_for_jobs_that_pass_the_title_check(load_fixture, client_for):
    company = Company(name="Freshworks", ats=ATS.smartrecruiters, id="Freshworks")
    listing = load_fixture("smartrecruiters_freshworks_list.json")
    detail = load_fixture("smartrecruiters_freshworks_detail.json")
    store, detail_calls = MemoryStore(), []
    board = {"content": []}

    def handler(request):
        if request.url.path.endswith("/postings"):
            return httpx.Response(200, json={**board, "totalFound": len(board["content"])})
        detail_calls.append(request.url.path)
        return httpx.Response(200, json=detail)

    http = client_for(handler)
    evaluator = Evaluator(CONFIG.model_copy(update={"require_level_match": False}), None, None)
    run_once(store, [company], http, evaluator=evaluator)  # baseline: no details at all
    assert detail_calls == []

    board["content"] = copy.deepcopy(listing["content"])
    board["content"][0]["name"] = "Data Analyst"
    board["content"][0]["location"]["fullLocation"] = "Bengaluru, KA, India"
    result = run_once(store, [company], http, evaluator=evaluator)
    # Only the data role needed details; the two sales roles failed on title alone.
    assert detail_calls == ["/v1/companies/Freshworks/postings/744000157728799"]
    (match,) = sort_matches(result.evaluated)
    assert match.job.description_text  # filled in from the detail call
    assert store.jobs[match.job.id]["description_text"] == match.job.description_text
