# InternRadar

A personal job-alert bot. It checks the career pages of companies **you** pick,
several times a day, and (soon) pings you on Telegram the moment a new matching
internship or fresher role appears, so you can apply on day 1.

> **Status: work in progress.** Phases 1–3 are done: fetching jobs from career pages,
> detecting new jobs with state stored in Supabase, and filtering + AI scoring against
> your resume. Telegram alerts, scheduling, the dashboard and the cold-mail tracker
> come next. The full beginner guide will be here once those land.

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

## How "new" is decided

- A job is **new** only if its id has never been seen before.
- The **first time a company is checked**, all of its current jobs are saved silently
  (a *baseline*), so you don't get hundreds of alerts for old jobs.
  `--alert-on-baseline` turns this off, for testing only.
- A **re-post** (new id, same company + title + location within 14 days) is saved
  but never alerted.
- If a fetch **fails** or suddenly returns **0 jobs**, nothing is marked missing.
  A job is closed only after it's absent from **2 successful runs in a row**.
- Running twice in a row never finds the same job twice.

## Set up Supabase (free) for Phase 2

1. Create a project at [supabase.com](https://supabase.com) (free plan). The Mumbai
   region is closest if you're in India. Save the database password somewhere safe.
2. Open **SQL Editor → New query**, paste all of
   `supabase/migrations/20261027000001_core.sql`, and click **Run**. Then do the
   same with `20261027000002_rls.sql`. Both should say "Success. No rows returned".
3. Open **Project Settings → API Keys** (or **Data API**) and copy the **Project URL**
   and a **secret** key (`sb_secret_...`). The legacy `service_role` key works too.
   Put them in `.env`:
   ```
   SUPABASE_URL=https://xxxx.supabase.co
   SUPABASE_SERVICE_ROLE_KEY=sb_secret_...
   ```
   This key can read and write everything, so keep it out of git and out of the web app.

```bash
python -m internradar run --company Groww     # first time: baseline, 0 new
python -m internradar run --company Groww     # again: still 0 new (idempotent)
python -m internradar run --dry-run           # read-only: shows what would be new
python -m internradar run                     # every enabled company
```

To check the SQL without Supabase: `cd supabase/verify && npm install && npm test`
runs the migrations in PGlite (Postgres in WebAssembly) and checks the functions
and the Row-Level Security policies.

## Filtering and AI scoring (Phase 3)

1. **Rule filter** (free, instant): [`filters.yaml`](filters.yaml) checks the role
   keywords in the title, rules out senior titles and "3+ years" asks, and keeps only
   Bengaluru or India-eligible remote jobs. Every job's verdict is saved with the reason,
   for example `level: title says 'senior'`. The file only seeds the database once;
   later you'll edit filters from Telegram or the dashboard.
2. **LLM score** (only for new jobs that pass): your `resume.md` and the job description
   go to the LLM, which returns `{"score": 1-10, "why": "...", "missing_skills": [...]}`.
   Bad JSON or an API error is retried once. If it still fails, the job keeps a blank
   score and is **still alerted**. Each job is scored at most once.

Setup:

- Get a free key at [console.groq.com/keys](https://console.groq.com/keys) and put it in
  `.env` as `GROQ_API_KEY=gsk_...`. The default model is `openai/gpt-oss-20b`.
  Groq's free tier allows about 30 requests and 8K tokens per minute, so a busy run
  pauses between jobs, which is normal.
- Save your resume as plain text or Markdown in `resume.md` at the repo root. It's
  git-ignored and only ever sent to the LLM.
- Other providers: set `LLM_PROVIDER` to `openai`, `gemini` or `ollama` and `LLM_MODEL`
  (see `.env.example`).

```bash
python -m internradar run --dry-run --alert-on-baseline --company "Sarvam AI"
```

## Being a good citizen

- A clear User-Agent with your contact email, so site owners can reach you.
- At most 1 request per second per domain, plus retries with backoff on 429/5xx and 15 s timeouts.
- robots.txt is checked before any HTML page is scraped.
- Never LinkedIn, Naukri or Indeed. No logins, no CAPTCHA bypassing. Personal use only.

## License

MIT. See [LICENSE](LICENSE).
