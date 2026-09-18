-- ============================================================================
-- ORACOOL AI — COMPLETE SUPABASE SETUP (Patch 9 + persistence fixes)
-- ============================================================================
-- Run the WHOLE file in your project's Supabase Dashboard → SQL Editor.
-- It creates/updates the three public tables actually used by this backend.
-- Re-running against the supported schema is safe: existing records are kept.
-- Take a database backup first if you have customized these tables.
--
-- Includes:
--   subscribers : paid-plan/admin-grant records
--   user_flags  : suspension state, login activity, verification metadata
--   case_store  : private JSON snapshots, keyed by:
--                 main / conversations / alerts / brand_accounts / reminders
--
-- Important:
--   * This does NOT create Auth users or set/change anyone's password.
--   * It does NOT configure email providers, OAuth, payment keys or model policy.
--   * It does NOT seed empty snapshots or erase existing conversations.
--   * Phone dispatch reservations use additional reminder_dispatch_<id> keys.
--   * Media files remain on server disk; use persistent storage/downloads.
--   * Backend-only tables: RLS ON; anon/authenticated roles have NO access.
--   * SUPABASE_SERVICE_KEY belongs on the SERVER, never in browser JavaScript.
--   * Admin identity remains controlled by server-side ADMIN_EMAILS and login.
-- ============================================================================

begin;
set local lock_timeout = '10s';
set local statement_timeout = '120s';

-- Supabase PostgreSQL includes gen_random_uuid(); no extra extension is needed.
create table if not exists public.subscribers (
    id             uuid primary key default gen_random_uuid(),
    email          text not null,
    tier           text default 'pro',
    plan           text,
    reference      text,
    amount_ngn     numeric,
    amount_usd     numeric,
    paid_at        text,
    expires_at     text,
    channel        text,
    days           integer,
    "by"           text,
    subscribed_at  timestamptz default now(),
    created_at     timestamptz default now()
);

-- Repair missing columns in earlier OraCool installations without dropping data.
-- Existing columns keep their types and values.
alter table public.subscribers
    add column if not exists id uuid default gen_random_uuid(),
    add column if not exists email text,
    add column if not exists tier text default 'pro',
    add column if not exists plan text,
    add column if not exists reference text,
    add column if not exists amount_ngn numeric,
    add column if not exists amount_usd numeric,
    add column if not exists paid_at text,
    add column if not exists expires_at text,
    add column if not exists channel text,
    add column if not exists days integer,
    add column if not exists "by" text,
    add column if not exists subscribed_at timestamptz default now(),
    add column if not exists created_at timestamptz default now();

-- Email is NOT unique here: one account may have multiple payment records.
-- Historical payment dates remain paid_at; subscribed_at added to legacy rows
-- represents this migration's default, not proof of the original payment date.
create index if not exists subscribers_email_idx
    on public.subscribers (email);
create index if not exists oracool_subscribers_reference_idx
    on public.subscribers (reference);
create index if not exists oracool_subscribers_subscribed_at_idx
    on public.subscribers (subscribed_at desc);

create table if not exists public.user_flags (
    email                        text primary key,
    created                      text,
    last_seen                    text,
    last_ip                      text,
    blocked                      boolean default false,
    block_reason                 text,
    blocked_by                   text,
    blocked_at                   text,
    verified                     boolean,
    email_verification_required  boolean default false,
    updated_at                   timestamptz default now()
);

alter table public.user_flags
    add column if not exists email text,
    add column if not exists created text,
    add column if not exists last_seen text,
    add column if not exists last_ip text,
    add column if not exists blocked boolean default false,
    add column if not exists block_reason text,
    add column if not exists blocked_by text,
    add column if not exists blocked_at text,
    add column if not exists verified boolean,
    add column if not exists email_verification_required boolean default false,
    add column if not exists updated_at timestamptz default now();

-- Upsert needs a unique email key. The extra named index also handles a legacy
-- table lacking a primary key. Duplicate legacy emails cause a safe rollback;
-- the script does not guess which user's record to delete.
create unique index if not exists oracool_user_flags_email_uidx
    on public.user_flags (email);
create index if not exists user_flags_blocked_idx
    on public.user_flags (blocked);

create table if not exists public.case_store (
    k           text primary key,
    v           jsonb not null,
    updated_at  timestamptz default now()
);

alter table public.case_store
    add column if not exists k text,
    add column if not exists v jsonb,
    add column if not exists updated_at timestamptz default now();

create unique index if not exists oracool_case_store_k_uidx
    on public.case_store (k);

comment on table public.subscribers is
    'OraCool backend-only subscription and payment records. Not a password store.';
comment on table public.user_flags is
    'OraCool backend-only account flags and activity. Not an admin-role authority.';
comment on table public.case_store is
    'OraCool backend-only JSON snapshots: main, conversations, alerts, brand_accounts.';

-- These snapshots may contain records for multiple accounts; NEVER expose them
-- directly through permissive policies for ordinary authenticated clients.
-- Application endpoints enforce account ownership and admin authorization.
alter table public.subscribers enable row level security;
alter table public.user_flags enable row level security;
alter table public.case_store enable row level security;

-- Revoke existing direct table grants, including PUBLIC (inherited by all roles).
revoke all privileges on table public.subscribers from public, anon, authenticated;
revoke all privileges on table public.user_flags from public, anon, authenticated;
revoke all privileges on table public.case_store from public, anon, authenticated;

-- Also remove any legacy per-column grants on these three tables.
do $oracool$
declare
    tbl text;
    cols text;
begin
    foreach tbl in array array['subscribers', 'user_flags', 'case_store'] loop
        select string_agg(quote_ident(column_name), ', ' order by ordinal_position)
          into cols
          from information_schema.columns
         where table_schema = 'public' and table_name = tbl;
        execute format(
            'revoke select (%s), insert (%s), update (%s), references (%s) on table public.%I from public, anon, authenticated',
            cols, cols, cols, cols, tbl
        );
    end loop;
end;
$oracool$;

-- Supabase service_role already has BYPASSRLS. Do NOT assign that role to users.
-- Grant only the data operations the backend needs; no public read/write policy.
grant usage on schema public to service_role;
grant select, insert, update, delete on table public.subscribers to service_role;
grant select, insert, update, delete on table public.user_flags to service_role;
grant select, insert, update, delete on table public.case_store to service_role;

-- Make new tables/columns visible to the Supabase REST API after commit.
notify pgrst, 'reload schema';
commit;

-- Verification: exactly three rows; RLS true, public reads false, service reads true.
select c.relname as table_name,
       c.relrowsecurity as rls_enabled,
       has_table_privilege('anon', c.oid, 'SELECT') as anon_can_read,
       has_table_privilege('authenticated', c.oid, 'SELECT') as user_can_read,
       has_table_privilege('service_role', c.oid, 'SELECT') as backend_can_read
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
 where n.nspname = 'public'
   and c.relname in ('subscribers', 'user_flags', 'case_store')
 order by c.relname;
