-- Applied using the radar_private_telegram_bridge migration.
-- These tables deliberately have no anon/authenticated policies or grants.
-- Only the Edge Function service role can read or write them.
create table public.radar_bridge_config (
  id text primary key, owner_id text not null, bot_id text not null, key_hash text not null
);
alter table public.radar_bridge_config enable row level security;
revoke all on public.radar_bridge_config from anon, authenticated;
create table public.radar_bridge_jobs (
  id uuid primary key default gen_random_uuid(), owner_id text not null,
  request jsonb not null, status text not null default 'pending'
    check(status in ('pending','running','done','failed')),
  response jsonb, created_at timestamptz not null default now(),
  claimed_at timestamptz, finished_at timestamptz
);
alter table public.radar_bridge_jobs enable row level security;
revoke all on public.radar_bridge_jobs from anon, authenticated;
create index radar_bridge_jobs_pending on public.radar_bridge_jobs(status,created_at);
grant all on public.radar_bridge_config, public.radar_bridge_jobs to service_role;
