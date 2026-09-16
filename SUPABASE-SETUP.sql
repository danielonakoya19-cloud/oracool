-- ============================================================
--  OraCool AI — Supabase schema (run ONCE in the SQL Editor)
--  Supabase dashboard → SQL Editor → New query → paste → Run
-- ============================================================
--  Why: OraCool keeps the *real* user list in Supabase Auth, but
--  three helper tables are needed so data survives (Render's disk
--  resets on every restart):
--     1) public.subscribers — Paystack payments & PRO subscriptions
--     2) public.user_flags   — block/ban + last-seen (admin console)
--     3) public.case_store   — evidence/case/custody/audit snapshots
--
--  The app only reads/writes these with the service_role key from
--  the server, which bypasses Row Level Security. RLS stays ON with
--  no public policies, so no website visitor can touch them.

-- The subscribers table uses gen_random_uuid(); make sure it's available.
create extension if not exists pgcrypto;

-- ------------------------------------------------------------
-- 1) Subscribers (PRO tiers from Paystack / admin grants)
-- ------------------------------------------------------------
create table if not exists public.subscribers (
  id         uuid primary key default gen_random_uuid(),
  email      text not null,
  tier       text default 'pro',
  plan       text,
  reference  text,
  amount_ngn numeric,
  paid_at    text,
  expires_at text,
  channel    text,
  days       int,
  created_at timestamptz default now()
);

-- ------------------------------------------------------------
-- 2) User flags (persistent block/ban + last-seen for admins)
-- ------------------------------------------------------------
create table if not exists public.user_flags (
  email        text primary key,
  created      text,
  last_seen    text,
  blocked      boolean default false,
  block_reason text,
  blocked_by   text,
  blocked_at   text,
  updated_at   timestamptz default now()
);

-- ------------------------------------------------------------
-- 3) Case store — durable snapshot of investigations (cases, evidence
--    metadata + bounded artifact contents, custody + audit trail).
--    Render's disk is ephemeral; this single-row jsonb mirror makes
--    evidence survive redeploys. Written only by the backend.
-- ------------------------------------------------------------
create table if not exists public.case_store (
  k          text primary key,           -- always 'main'
  v          jsonb not null,
  updated_at timestamptz default now()
);

-- ------------------------------------------------------------
-- Lock down: only the backend (service_role) may access these.
-- ------------------------------------------------------------
alter table public.subscribers enable row level security;
alter table public.user_flags   enable row level security;
alter table public.case_store    enable row level security;

-- (No policies are added on purpose: anon/authenticated clients are
--  denied by RLS, while the server's service_role key bypasses RLS.)

-- ------------------------------------------------------------
-- Indexes for fast lookups by email
-- ------------------------------------------------------------
create index if not exists subscribers_email_idx on public.subscribers (email);
create index if not exists user_flags_blocked_idx on public.user_flags (blocked);
