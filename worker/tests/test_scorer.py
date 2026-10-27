"""LLM scoring: strict JSON, retry once, never drop a job; and the provider client."""

from __future__ import annotations

import json

import httpx
import pytest

from internradar.http import PoliteClient
from internradar.llm import LLMError, OpenAICompatibleLLM, llm_from_env
from internradar.models import ATS, Job
from internradar.scorer import DESCRIPTION_CHARS, build_prompt, parse_score, score_job

JOB = Job(
    id="x",
    company="Acme",
    ats_job_id="1",
    title="ML Intern",
    location="Bengaluru",
    url="https://x",
    description_text="Python, PyTorch. " * 1000,
    source_ats=ATS.lever,
)


class FakeLLM:
    name = "fake:model"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.prompts.append(user)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


GOOD = '{"score": 8, "why": "Strong Python and ML projects", "missing_skills": ["Docker"]}'


@pytest.mark.parametrize(
    "text",
    [
        GOOD,
        f"```json\n{GOOD}\n```",
        f"Sure! Here is the result: {GOOD} Hope that helps.",
        GOOD.replace("8", "8.0"),
    ],
)
def test_parse_score_accepts_reasonable_replies(text):
    result = parse_score(text)
    assert (result.score, result.why, result.missing_skills) == (
        8,
        "Strong Python and ML projects",
        ["Docker"],
    )


@pytest.mark.parametrize(
    "text",
    [
        "I think this is a 7",
        '{"score": 11, "why": "x", "missing_skills": []}',
        '{"score": 0, "why": "x"}',
        '{"score": "high", "why": "x"}',
        '{"score": 7}',
        '{"score": 7, "why": "  "}',
        "[1, 2]",
    ],
)
def test_parse_score_rejects_bad_replies(text):
    with pytest.raises(ValueError):
        parse_score(text)


def test_retries_once_after_invalid_json():
    llm = FakeLLM("not json", GOOD)
    scored = score_job(llm, "my resume", JOB)
    assert scored.result is not None and scored.result.score == 8
    assert scored.calls == 2


def test_gives_up_after_two_failures_without_raising():
    llm = FakeLLM(LLMError("HTTP 500"), "still not json")
    scored = score_job(llm, "my resume", JOB)
    assert scored.result is None and scored.calls == 2
    assert scored.error == "reply is not JSON"


def test_prompt_has_resume_and_truncated_description():
    prompt = build_prompt("MY RESUME TEXT", JOB)
    assert "MY RESUME TEXT" in prompt and "ML Intern at Acme" in prompt
    assert len(prompt) < DESCRIPTION_CHARS + 300


def _llm(provider, handler):
    http = PoliteClient(
        min_interval=0, transport=httpx.MockTransport(handler), sleep=lambda s: None
    )
    return OpenAICompatibleLLM(provider, "some-model", "sk-SECRET", http=http)


def test_groq_request_shape():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": GOOD}}]})

    assert _llm("groq", handler).complete("sys", "user") == GOOD
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-SECRET"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in seen["body"]["messages"]] == ["system", "user"]


def test_gemini_skips_json_mode():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": GOOD}}]})

    _llm("gemini", handler).complete("sys", "user")
    assert "response_format" not in bodies[0]


def test_api_errors_become_llm_errors_without_the_key():
    def handler(request):
        return httpx.Response(401, json={"error": "invalid key"})

    with pytest.raises(LLMError) as info:
        _llm("groq", handler).complete("sys", "user")
    assert "SECRET" not in str(info.value)


def test_llm_from_env(monkeypatch):
    for var in ("LLM_PROVIDER", "LLM_MODEL", "GROQ_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    llm, why = llm_from_env()
    assert llm is None and "GROQ_API_KEY" in why

    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    llm, why = llm_from_env()
    assert llm is not None and llm.name == "groq:openai/gpt-oss-20b"

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    llm, why = llm_from_env()
    assert llm is None and "LLM_MODEL" in why
    monkeypatch.setenv("LLM_MODEL", "llama3.2")
    llm, _ = llm_from_env()
    assert llm is not None and llm.name == "ollama:llama3.2"
