-- OraCool AI patch60 — power ecosystem: personal API keys + AMA + build showcase
-- Run once in Supabase Dashboard → SQL Editor (production project). Safe to re-run.

-- 1) Personal API keys (full keys are never stored — only SHA-256 hashes)
create table if not exists public.api_keys (
  id bigint generated always as identity primary key,
  email text not null,
  name text default 'default',
  key_hash text unique not null,
  prefix text,
  last4 text,
  created_at timestamptz default now(),
  last_used_at timestamptz,
  requests bigint default 0,
  revoked boolean default false
);
create index if not exists api_keys_email_idx on public.api_keys (email);

alter table public.api_keys enable row level security;
drop policy if exists "api_keys service role all" on public.api_keys;
create policy "api_keys service role all"
on public.api_keys for all to service_role using (true) with check (true);

-- 2) Ask-Me-Anything sessions hosted by power users
create table if not exists public.comm_amas (
  id bigint generated always as identity primary key,
  host_email text not null,
  title text not null,
  description text default '',
  status text default 'live',
  created_at timestamptz default now(),
  started_at timestamptz,
  ended_at timestamptz
);

-- 3) Audience questions (upvotable, answered inline by the host)
create table if not exists public.comm_ama_questions (
  id bigint generated always as identity primary key,
  ama_id bigint not null,
  asker_email text not null,
  body text not null,
  votes integer default 0,
  answered boolean default false,
  answer_text text default '',
  answered_at timestamptz,
  created_at timestamptz default now()
);
create index if not exists ama_questions_ama_idx on public.comm_ama_questions (ama_id);

-- 4) Build showcase — members share published sites (word-of-mouth engine)
create table if not exists public.comm_showcase (
  id bigint generated always as identity primary key,
  email text not null,
  title text default 'My build',
  url text not null,
  description text default '',
  likes integer default 0,
  created_at timestamptz default now()
);

create table if not exists public.comm_showcase_likes (
  id bigint generated always as identity primary key,
  item_id bigint not null,
  voter_email text not null,
  created_at timestamptz default now(),
  unique (item_id, voter_email)
);

alter table public.comm_amas enable row level security;
alter table public.comm_ama_questions enable row level security;
alter table public.comm_showcase enable row level security;
alter table public.comm_showcase_likes enable row level security;

drop policy if exists "comm_amas service role all" on public.comm_amas;
create policy "comm_amas service role all" on public.comm_amas for all to service_role using (true) with check (true);
drop policy if exists "comm_ama_questions service role all" on public.comm_ama_questions;
create policy "comm_ama_questions service role all" on public.comm_ama_questions for all to service_role using (true) with check (true);
drop policy if exists "comm_showcase service role all" on public.comm_showcase;
create policy "comm_showcase service role all" on public.comm_showcase for all to service_role using (true) with check (true);
drop policy if exists "comm_showcase_likes service role all" on public.comm_showcase_likes;
create policy "comm_showcase_likes service role all" on public.comm_showcase_likes for all to service_role using (true) with check (true);

grant all on public.api_keys to service_role;
grant all on public.comm_amas to service_role;
grant all on public.comm_ama_questions to service_role;
grant all on public.comm_showcase to service_role;
grant all on public.comm_showcase_likes to service_role;

-- Quick checks after running:
-- select count(*) from public.api_keys;
-- select count(*) from public.comm_amas;
-- select count(*) from public.comm_showcase;
