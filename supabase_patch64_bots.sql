-- OraCool AI patch64 — community bots (BotFather-style)
-- Run once in Supabase Dashboard → SQL Editor (production project). Safe to re-run.

-- Bot tokens are NEVER stored in clear — only SHA-256 hashes.
create table if not exists public.comm_bots (
  id bigint generated always as identity primary key,
  owner_email text not null,
  name text not null,
  username text unique not null,          -- must end with 'bot' (enforced by the app)
  token_hash text unique not null,
  token_prefix text,
  token_last4 text,
  brain text default '',                  -- personality & purpose written by the creator
  greeting text default '',
  enabled boolean default true,
  replies bigint default 0,
  created_at timestamptz default now()
);
create index if not exists comm_bots_owner_idx on public.comm_bots (owner_email);

alter table public.comm_bots enable row level security;
drop policy if exists "comm_bots service role all" on public.comm_bots;
create policy "comm_bots service role all"
on public.comm_bots for all to service_role using (true) with check (true);

grant all on public.comm_bots to service_role;

-- Bot messages land in the existing comm_messages table with
-- sender_email = 'bot:<username>' — no new table needed.

-- Quick check after running:
-- select count(*) from public.comm_bots;
