"""Guess a company's ATS and board id from its careers URL, then verify it.

    py detect_ats.py https://jobs.lever.co/meesho
    py detect_ats.py https://www.example.com/careers --name "Example Co"

1. Look for a known ATS pattern in the URL itself.
2. Otherwise fetch the page (if robots.txt allows), follow redirects and look
   for ATS links in the final URL and in the HTML.
3. Verify the guess by calling the real ATS API and counting open jobs.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlsplit

import yaml

from internradar.adapters import get_adapter
from internradar.config import contact_email, load_env
from internradar.http import FetchError, PoliteClient
from internradar.models import ATS, Company

_SLUG = r"([A-Za-z0-9][A-Za-z0-9_.-]*)"

# Order matters: more specific patterns first.
PATTERNS: list[tuple[ATS, re.Pattern[str]]] = [
    (ATS.greenhouse, re.compile(r"greenhouse\.io/embed/job_board(?:/js)?\?for=" + _SLUG)),
    (ATS.greenhouse, re.compile(r"boards-api\.greenhouse\.io/v1/boards/" + _SLUG)),
    (ATS.greenhouse, re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?!embed\b)" + _SLUG)),
    (ATS.lever, re.compile(r"api\.lever\.co/v0/postings/" + _SLUG)),
    (ATS.lever, re.compile(r"jobs\.lever\.co/" + _SLUG)),
    (ATS.ashby, re.compile(r"api\.ashbyhq\.com/posting-api/job-board/" + _SLUG)),
    (ATS.ashby, re.compile(r"jobs\.ashbyhq\.com/" + _SLUG)),
    (ATS.smartrecruiters, re.compile(r"api\.smartrecruiters\.com/v1/companies/" + _SLUG)),
    (ATS.smartrecruiters, re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/" + _SLUG)),
    # {subdomain}.keka.com/careers ("cdn.keka.com" only hosts Keka's own scripts)
    (ATS.keka, re.compile(r"(?<![a-z0-9-])(?!cdn\.)([a-z0-9][a-z0-9-]*)\.keka\.com/careers")),
]
WORKDAY = re.compile(
    r"([a-z0-9-]+)\.wd(\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?(?!wday\b)([A-Za-z0-9_-]+)"
)


@dataclass
class Guess:
    ats: ATS
    id: str
    workday_dc: int | None = None
    workday_site: str | None = None


def guesses_in(text: str) -> list[Guess]:
    """Every ATS reference found in a URL or HTML page, in order of appearance."""
    found: list[tuple[int, Guess]] = []
    for ats, pattern in PATTERNS:
        for m in pattern.finditer(text):
            found.append((m.start(), Guess(ats, m.group(1).rstrip("."))))
    for m in WORKDAY.finditer(text):
        found.append((m.start(), Guess(ATS.workday, m.group(1), int(m.group(2)), m.group(3))))
    return [g for _, g in sorted(found, key=lambda x: x[0])]


def best_guess(text: str) -> Guess | None:
    guesses = guesses_in(text)
    if not guesses:
        return None
    counts = Counter((g.ats, g.id.lower(), g.workday_site) for g in guesses)
    top = counts.most_common(1)[0][0]
    return next(g for g in guesses if (g.ats, g.id.lower(), g.workday_site) == top)


def detect(url: str, http: PoliteClient) -> Guess | None:
    guess = best_guess(url)
    if guess:
        return guess
    if not http.allowed_by_robots(url):
        raise FetchError(f"robots.txt does not allow fetching {url}")
    response = http.request("GET", url)
    return best_guess(str(response.url)) or best_guess(response.text)


def to_company(guess: Guess, name: str, careers_url: str) -> Company:
    return Company(
        name=name,
        ats=guess.ats,
        id=guess.id,
        workday_dc=guess.workday_dc,
        workday_site=guess.workday_site,
        careers_url=careers_url,
    )


def guess_name(url: str, guess: Guess) -> str:
    if guess.ats is ATS.workday or "." not in guess.id:
        return guess.id.replace("-", " ").replace("_", " ").title()
    return urlsplit(url).netloc.split(".")[-2].title()


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url")
    parser.add_argument("--name", help="company name to use in companies.yaml")
    args = parser.parse_args(argv)

    with PoliteClient(contact_email()) as http:
        try:
            guess = detect(args.url, http)
        except FetchError as exc:
            print(f"Could not read {args.url}: {exc}", file=sys.stderr)
            return 1
        if guess is None:
            print(
                "No known ATS found on that page. Many careers pages load jobs with "
                "JavaScript: open one job posting in your browser and run this again with "
                "that job's link. If the site really has no ATS, add it with ats: html and "
                "CSS selectors (see CONTRIBUTING.md).",
                file=sys.stderr,
            )
            return 1

        company = to_company(guess, args.name or guess_name(args.url, guess), args.url)
        try:
            count = get_adapter(company.ats, http).probe(company)
        except FetchError as exc:
            print(f"Guessed {company.ats} / {company.id}, but the API call failed: {exc}")
            return 1

    print(f"✓ Verified: {company.ats} / {company.id} — {count} open jobs right now\n")
    if company.ats is ATS.workday:
        print("Tip: add workday_search (e.g. Bangalore) so runs don't page through every job.\n")
    print("Add this to companies.yaml:\n")
    entry = company.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    entry = {"name": entry.pop("name"), "ats": entry.pop("ats"), "id": entry.pop("id"), **entry}
    print(yaml.safe_dump([entry], sort_keys=False, allow_unicode=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
