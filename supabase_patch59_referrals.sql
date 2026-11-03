-- OraCool AI patch59 — referral / advocate program tables
-- Run once in Supabase Dashboard → SQL Editor (production project). Safe to re-run.

-- 1) Advocate counters live on the durable flags row (survive redeploys)
alter table public.user_flags add column if not exists ref_code text;
alter table public.user_flags add column if not exists referred_by text;
alter table public.user_flags add column if not exists ref_count integer default 0;
alter table public.user_flags add column if not exists ref_coins_earned bigint default 0;
alter table public.user_flags add column if not exists early_access boolean default false;

create unique index if not exists user_flags_ref_code_key
  on public.user_flags (ref_code)
  where ref_code is not null and ref_code <> '';

-- 2) One row per friend who joins through a referral link
create table if not exists public.referrals (
  id bigint generated always as identity primary key,
  referrer_email text not null,
  referred_email text not null unique,
  code text,
  coins_awarded bigint default 0,
  created_at timestamptz default now()
);

alter table public.referrals enable row level security;

drop policy if exists "referrals service role all" on public.referrals;
create policy "referrals service role all"
on public.referrals for all to service_role using (true) with check (true);

-- 3) Leaderboard for the admin board
create or replace view public.oracool_top_advocates as
select
  r.referrer_email,
  f.ref_count,
  f.ref_coins_earned,
  f.early_access,
  count(*) as joined,
  sum(r.coins_awarded) as coins_awarded,
  max(r.created_at) as last_referral
from public.referrals r
left join public.user_flags f on f.email = r.referrer_email
group by r.referrer_email, f.ref_count, f.ref_coins_earned, f.early_access
order by sum(r.coins_awarded) desc nulls last
limit 100;

grant all on public.referrals to service_role;
grant select on public.oracool_top_advocates to service_role;

-- Quick check after running:
-- select count(*) from public.referrals;
-- select * from public.oracool_top_advocates limit 10;
