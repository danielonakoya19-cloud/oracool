-- OraCool AI patch61 — user wallet: $1 per active referral + withdrawals
-- Run once in Supabase Dashboard → SQL Editor (production project). Safe to re-run.

-- 1) Wallet balances live on the durable flags row
alter table public.user_flags add column if not exists wallet_available_cents bigint default 0;
alter table public.user_flags add column if not exists wallet_pending_cents bigint default 0;
alter table public.user_flags add column if not exists wallet_paidout_cents bigint default 0;

-- 2) Referrals become a two-stage payout: pending → earned when the friend
--    actually uses OraCool (their first chat)
alter table public.referrals add column if not exists status text default 'pending';
alter table public.referrals add column if not exists earned_at timestamptz;
create index if not exists referrals_referred_status_idx on public.referrals (referred_email, status);

-- 3) Withdrawal requests (paid manually by the creator)
create table if not exists public.wallet_withdrawals (
  id bigint generated always as identity primary key,
  email text not null,
  amount_cents bigint not null,
  method text not null,
  destination text not null,
  status text default 'requested',
  created_at timestamptz default now(),
  paid_at timestamptz,
  paid_by text
);
create index if not exists wallet_withdrawals_email_idx on public.wallet_withdrawals (email);
create index if not exists wallet_withdrawals_status_idx on public.wallet_withdrawals (status);

alter table public.wallet_withdrawals enable row level security;
drop policy if exists "wallet_withdrawals service role all" on public.wallet_withdrawals;
create policy "wallet_withdrawals service role all"
on public.wallet_withdrawals for all to service_role using (true) with check (true);

grant all on public.wallet_withdrawals to service_role;

-- Quick checks after running:
-- select count(*) from public.wallet_withdrawals where status = 'requested';
-- select email, wallet_available_cents, wallet_pending_cents from public.user_flags
--   where coalesce(wallet_available_cents,0) + coalesce(wallet_pending_cents,0) > 0;
