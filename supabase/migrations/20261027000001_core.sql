-- InternRadar core schema: companies, runs, jobs, per-company fetch log, settings.
-- Run this first in the Supabase SQL editor (or with `supabase db push`).

create type ats_type as enum ('greenhouse', 'lever', 'ashby', 'smartrecruiters', 'workday', 'html');
create type job_status as enum ('new', 'interested', 'applied', 'referral_asked', 'skipped');
create type run_status as enum ('running', 'success', 'partial', 'failed');

-- Companies being tracked. companies.yaml only seeds this table; after that the
-- table is the source of truth (toggles from the dashboard/Telegram stick).
create table companies (
  id                   uuid primary key default gen_random_uuid(),
  name                 text not null unique,
  ats                  ats_type not null,
  ats_id               text not null,              -- board token / slug / tenant
  workday_dc           int,
  workday_site         text,
  workday_search       text,
  careers_url          text,
  html_selectors       jsonb,
  enabled              boolean not null default true,
  source               text not null default 'yaml' check (source in ('yaml', 'telegram', 'dashboard')),
  baselined_at         timestamptz,                -- null => next successful fetch is a silent baseline
  last_success_at      timestamptz,
  last_error           text,
  last_error_at        timestamptz,
  consecutive_failures int not null default 0,
  open_jobs_count      int not null default 0,
  created_at           timestamptz not null default now(),
  unique (ats, ats_id)
);

-- One row per checker run (scheduled, manual or triggered by /add).
create table runs (
  id                uuid primary key default gen_random_uuid(),
  trigger           text not null default 'manual',
  status            run_status not null default 'running',
  started_at        timestamptz not null default now(),
  finished_at       timestamptz,
  companies_checked int not null default 0,
  companies_failed  int not null default 0,
  new_jobs          int not null default 0,
  alerts_sent       int not null default 0,
  llm_calls         int not null default 0,
  duration_ms       int,
  error             text
);

-- Every job ever seen. id = stable hash computed by the worker.
create table jobs (
  id                text primary key,
  company_id        uuid not null references companies (id) on delete cascade,
  ats_job_id        text,
  title             text not null,
  title_norm        text not null,
  location          text not null default '',
  is_remote         boolean not null default false,
  department        text,
  url               text not null,
  posted_at         timestamptz,                   -- from the ATS, if given
  first_seen_at     timestamptz not null default now(),  -- when OUR bot first saw it
  first_seen_run    uuid references runs (id) on delete set null,
  last_seen_at      timestamptz not null default now(),
  missing_runs      int not null default 0,        -- consecutive successful runs it was absent
  closed_at         timestamptz,                   -- set after 2 missing runs
  description_text  text not null default '',
  source_ats        ats_type not null,
  dedupe_key        text not null,                 -- company + normalised title + location
  duplicate_of      text references jobs (id) on delete set null,  -- re-post of this job: no alert
  is_baseline       boolean not null default false, -- saved on a company's first fetch: no alert
  -- Phase 3: rule filter + LLM score (score cached here, never recomputed)
  filter_passed     boolean,
  filter_reason     text,
  category          text,
  score             smallint check (score between 1 and 10),
  score_why         text,
  missing_skills    text[],
  scored_at         timestamptz,
  llm_model         text,
  -- Phase 4: alerts + your tracking
  alerted_at        timestamptz,
  status            job_status not null default 'new',
  status_updated_at timestamptz,
  notes             text
);

create index jobs_company_idx on jobs (company_id);
create index jobs_first_seen_idx on jobs (first_seen_at desc);
create index jobs_dedupe_idx on jobs (company_id, dedupe_key);
create index jobs_unalerted_idx on jobs (first_seen_at) where alerted_at is null and not is_baseline;

-- Per-company result of each run: feeds "failed 3 runs in a row" and the Companies tab.
create table company_fetches (
  id            bigint generated always as identity primary key,
  run_id        uuid not null references runs (id) on delete cascade,
  company_id    uuid not null references companies (id) on delete cascade,
  ok            boolean not null,
  jobs_returned int not null default 0,
  error         text,
  duration_ms   int,
  created_at    timestamptz not null default now()
);
create index company_fetches_company_idx on company_fetches (company_id, created_at desc);

-- Single-row settings table (pause, min score, filters, Gmail query).
create table settings (
  id          int primary key default 1 check (id = 1),
  paused      boolean not null default false,
  min_score   smallint check (min_score between 1 and 10),
  filters     jsonb,
  gmail_query text not null default 'in:sent subject:(internship OR intern)',
  updated_at  timestamptz not null default now()
);
insert into settings (id) values (1);

-- Called by the worker after a SUCCESSFUL fetch of one company, after it has
-- inserted the new jobs. Applies seen / missing / closed updates atomically.
create function apply_company_sync(
  p_company_id  uuid,
  p_run_id      uuid,
  p_seen        text[],
  p_missing     text[],
  p_closed      text[],
  p_baseline    boolean,
  p_open_count  int,
  p_duration_ms int,
  p_now         timestamptz
) returns void
language plpgsql
set search_path = public
as $$
begin
  -- Seen this run: refresh, reset the missing counter, reopen if it had closed.
  update jobs set last_seen_at = p_now, missing_runs = 0, closed_at = null
   where company_id = p_company_id and id = any (p_seen);

  update jobs set missing_runs = missing_runs + 1
   where company_id = p_company_id and id = any (p_missing) and closed_at is null;

  update jobs set missing_runs = missing_runs + 1, closed_at = p_now
   where company_id = p_company_id and id = any (p_closed) and closed_at is null;

  update companies
     set last_success_at      = p_now,
         last_error           = null,
         consecutive_failures = 0,
         open_jobs_count      = p_open_count,
         baselined_at         = case when p_baseline then coalesce(baselined_at, p_now) else baselined_at end
   where id = p_company_id;

  insert into company_fetches (run_id, company_id, ok, jobs_returned, duration_ms)
  values (p_run_id, p_company_id, true, p_open_count, p_duration_ms);
end;
$$;

-- Called when a company's fetch fails. Existing jobs are left untouched.
-- Returns the new consecutive failure count.
create function record_company_failure(
  p_company_id  uuid,
  p_run_id      uuid,
  p_error       text,
  p_duration_ms int,
  p_now         timestamptz
) returns int
language plpgsql
set search_path = public
as $$
declare
  failures int;
begin
  update companies
     set consecutive_failures = consecutive_failures + 1,
         last_error           = left(p_error, 1000),
         last_error_at        = p_now
   where id = p_company_id
  returning consecutive_failures into failures;

  insert into company_fetches (run_id, company_id, ok, error, duration_ms)
  values (p_run_id, p_company_id, false, left(p_error, 1000), p_duration_ms);

  return coalesce(failures, 0);
end;
$$;

-- Only the worker (service role) may call these.
revoke execute on function apply_company_sync (uuid, uuid, text[], text[], text[], boolean, int, int, timestamptz) from public, anon, authenticated;
revoke execute on function record_company_failure (uuid, uuid, text, int, timestamptz) from public, anon, authenticated;
grant execute on function apply_company_sync (uuid, uuid, text[], text[], text[], boolean, int, int, timestamptz) to service_role;
grant execute on function record_company_failure (uuid, uuid, text, int, timestamptz) to service_role;
