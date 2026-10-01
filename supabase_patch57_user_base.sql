-- OraCool AI patch57 — rebuild the Admin user base from Supabase Auth
-- Run once in Supabase Dashboard → SQL Editor (production project).
-- Safe to re-run.

create extension if not exists pgcrypto;

create table if not exists public.user_flags (
  email text primary key,
  created text default '',
  last_seen text default '',
  blocked boolean default false,
  block_reason text default '',
  blocked_by text default '',
  blocked_at text default '',
  verified boolean,
  last_ip text,
  coins text,
  email_verification_required boolean,
  updated_at timestamptz default now()
);

alter table public.user_flags add column if not exists created text default '';
alter table public.user_flags add column if not exists last_seen text default '';
alter table public.user_flags add column if not exists blocked boolean default false;
alter table public.user_flags add column if not exists block_reason text default '';
alter table public.user_flags add column if not exists blocked_by text default '';
alter table public.user_flags add column if not exists blocked_at text default '';
alter table public.user_flags add column if not exists verified boolean;
alter table public.user_flags add column if not exists last_ip text;
alter table public.user_flags add column if not exists coins text;
alter table public.user_flags add column if not exists email_verification_required boolean;
alter table public.user_flags add column if not exists updated_at timestamptz default now();

alter table public.user_flags enable row level security;

drop policy if exists "user_flags service role all" on public.user_flags;
create policy "user_flags service role all"
on public.user_flags for all to service_role using (true) with check (true);

-- Copy every Auth signup into user_flags so the Admin board survives Render disk wipes.
insert into public.user_flags (email, created, verified, updated_at)
select
  lower(u.email),
  to_char(u.created_at at time zone 'utc', 'YYYY-MM-DD HH24:MI:SS'),
  (u.email_confirmed_at is not null),
  now()
from auth.users u
where coalesce(u.email, '') <> ''
on conflict (email) do update
set created = coalesce(nullif(public.user_flags.created, ''), excluded.created),
    verified = excluded.verified,
    updated_at = now();

create or replace view public.oracool_user_base as
select
  lower(u.email) as email,
  u.created_at,
  u.last_sign_in_at,
  u.email_confirmed_at is not null as verified,
  f.last_seen as app_last_seen,
  coalesce(f.blocked, false) as blocked
from auth.users u
left join public.user_flags f on f.email = lower(u.email)
where coalesce(u.email, '') <> ''
order by u.created_at desc;

grant usage on schema public to authenticated, service_role;
grant all on public.user_flags to service_role;
grant select on public.oracool_user_base to service_role;

-- Quick check after running: you should see dozens of rows, not 1.
-- select count(*) from auth.users;
-- select count(*) from public.user_flags;
-- select * from public.oracool_user_base limit 50;
