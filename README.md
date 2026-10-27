# InternRadar

A personal job-alert bot. It checks the career pages of companies **you** pick,
several times a day, and (soon) pings you on Telegram the moment a new matching
internship or fresher role appears, so you can apply on day 1.

> **Status: work in progress.** Phase 1 (fetching jobs from career pages) is done.
> Database, filtering, AI scoring, Telegram alerts, scheduling, the dashboard and the
> cold-mail tracker are coming next. The full beginner guide will be here once those land.

## How it fetches jobs

Most companies host their jobs on an **Applicant Tracking System (ATS)** that
has a public JSON feed. InternRadar reads those feeds directly, with no scraping,
logging in or CAPTCHAs:

| ATS | Endpoint |
|---|---|
| Greenhouse | `GET boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` |
| Lever | `GET api.lever.co/v0/postings/{company}?mode=json` |
| Ashby | `GET api.ashbyhq.com/posting-api/job-board/{board}` |
| SmartRecruiters | `GET api.smartrecruiters.com/v1/companies/{id}/postings` |
| Workday | `POST {tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` |
| Anything else | HTML + CSS selectors from `companies.yaml` (respects robots.txt) |

## Try Phase 1 locally (Windows, Python 3.11+)

```bash
cd worker
py -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
copy ..\.env.example ..\.env      # then put your email in CONTACT_EMAIL
```

```bash
pytest                                        # offline tests on saved real responses
python -m internradar fetch --show 2          # live: every company in companies.yaml
python -m internradar fetch --company Intel --details --show 3
python detect_ats.py https://jobs.lever.co/meesho
```

## Being a good citizen

- A clear User-Agent with your contact email, so site owners can reach you.
- At most 1 request per second per domain, plus retries with backoff on 429/5xx and 15 s timeouts.
- robots.txt is checked before any HTML page is scraped.
- Never LinkedIn, Naukri or Indeed. No logins, no CAPTCHA bypassing. Personal use only.

## License

MIT. See [LICENSE](LICENSE).
