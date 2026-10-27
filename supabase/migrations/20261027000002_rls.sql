-- Row-Level Security. Run after the core migration.
--
-- The worker and /api use the service role key, which bypasses RLS.
-- The dashboard uses the anon key + your Google login, so it only sees rows if
-- your auth user id is in app_owner. Nothing about you is hard-coded here:
-- after your first dashboard login (Phase 6) you run, once:
--   insert into app_owner (user_id) select id from auth.users where email = 'you@example.com';

create table app_owner (
  user_id    uuid primary key references auth.users (id) on delete cascade,
  created_at timestamptz not null default now()
);

create function is_owner() returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (select 1 from app_owner where user_id = auth.uid());
$$;
revoke execute on function is_owner () from public, anon;
grant execute on function is_owner () to authenticated;

alter table app_owner       enable row level security;
alter table companies       enable row level security;
alter table runs            enable row level security;
alter table jobs            enable row level security;
alter table company_fetches enable row level security;
alter table settings        enable row level security;

create policy "owner sees own row" on app_owner
  for select to authenticated using (user_id = auth.uid());

create policy "owner reads companies" on companies
  for select to authenticated using (is_owner());
create policy "owner adds companies" on companies
  for insert to authenticated with check (is_owner());
create policy "owner edits companies" on companies
  for update to authenticated using (is_owner()) with check (is_owner());
create policy "owner removes companies" on companies
  for delete to authenticated using (is_owner());

create policy "owner reads jobs" on jobs
  for select to authenticated using (is_owner());
create policy "owner edits jobs" on jobs
  for update to authenticated using (is_owner()) with check (is_owner());

create policy "owner reads runs" on runs
  for select to authenticated using (is_owner());

create policy "owner reads fetches" on company_fetches
  for select to authenticated using (is_owner());

create policy "owner reads settings" on settings
  for select to authenticated using (is_owner());
create policy "owner edits settings" on settings
  for update to authenticated using (is_owner()) with check (is_owner());
