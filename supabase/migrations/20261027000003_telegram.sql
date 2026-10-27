-- Telegram webhook bookkeeping. Telegram re-sends an update if it doesn't get a
-- quick 2xx reply, so the webhook records each update_id and skips repeats.

create table telegram_updates (
  update_id   bigint primary key,
  received_at timestamptz not null default now()
);

-- Only the service role (the webhook) touches this table.
alter table telegram_updates enable row level security;

-- Status changes from the alert buttons are stored on jobs.status (already exists).
