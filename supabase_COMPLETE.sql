-- ============================================================================
-- OraCool · THE COMPLETE SUPABASE SQL — everything, one file (Nov 2026)
-- ============================================================================
-- HOW TO RUN (about 60 seconds):
--   1. Open your Supabase project → left menu "SQL Editor" → "New query".
--   2. Copy this WHOLE file, paste it, click "Run".
--   3. Done. Every statement is idempotent — safe to run again any time and
--      safe whether you have run older patch files or none of them.
--      Nothing here can lose data.
--
-- This ONE file = the full database (all parts, in the right order):
--   PART 1  Core platform   — community tables, website vaults, durable user
--                             flags/coins, RLS lockdown, server-only tables
--                             (was: supabase_complete.sql — supersedes patches
--                             15, 16, 18, 27 and the old setup files)
--   PART 2  Security tools  — gating tables + security console storage
--                             (was: supabase_patch52 + supabase_patch54)
--   PART 3  Admin user base — user_flags rebuild + oracool_user_base view
--                             (was: supabase_patch57_user_base.sql)
--   PART 4  Referrals       — referrals table + top-advocates leaderboard
--                             (was: supabase_patch59_referrals.sql)
--   PART 5  Power ecosystem — personal API keys, AMAs, build showcase
--                             (was: supabase_patch60_power_ecosystem.sql)
--   PART 6  Community bots  — comm_bots (BotFather-style bots)
--                             (was: supabase_patch64_bots.sql)
--
-- After running: you never need any other .sql file from this repo.
-- The running server picks everything up automatically — no redeploy needed.
-- ============================================================================
-- ============================================================================

-- ---------------------------------------------------------------- 1. Numbers
-- 10-digit OraCool numbers, issued by a sequence (unique by construction).
create sequence if not exists comm_number_seq
    start 2000000000
    increment 1
    no maxvalue
    cache 50;

-- --------------------------------------------------------------- 2. Profiles
create table if not exists public.comm_profiles (
    email          text primary key,
    username       text not null,
    display_name   text not null default '',
    oracool_number bigint not null default nextval('comm_number_seq'),
    bio            text not null default '',
    credibility    text not null default 'normal' check (credibility in ('normal','low')),
    avatar_url     text not null default '',
    verified_until timestamptz,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);

-- add the newer columns if this table was created by an older file
alter table public.comm_profiles add column if not exists avatar_url     text not null default '';
alter table public.comm_profiles add column if not exists verified_until timestamptz;

create unique index if not exists comm_profiles_username_uq on public.comm_profiles (lower(username));
create unique index if not exists comm_profiles_number_uq   on public.comm_profiles (oracool_number);
create index if not exists comm_profiles_display_idx        on public.comm_profiles (lower(display_name));

create or replace function comm_set_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists comm_profiles_touch on public.comm_profiles;
create trigger comm_profiles_touch
    before update on public.comm_profiles
    for each row execute function comm_set_updated_at();

-- ---------------------------------------------------------------- 3. Rooms
-- kind: 'room' = built-in public rooms · 'group' = member group
--       'channel' = member channel · 'dm' = private two-person thread
create table if not exists public.comm_rooms (
    id          uuid primary key default gen_random_uuid(),
    slug        text not null unique,
    name        text not null,
    kind        text not null default 'group' check (kind in ('room','group','channel','dm')),
    description text not null default '',
    is_public   boolean not null default true,
    owner_email text,
    banned      boolean not null default false,
    ban_reason  text not null default '',
    owner_only_post boolean not null default false,
    created_at  timestamptz not null default now()
);

-- add the newer columns if this table was created by an older file
alter table public.comm_rooms add column if not exists banned          boolean not null default false;
alter table public.comm_rooms add column if not exists ban_reason      text not null default '';
alter table public.comm_rooms add column if not exists owner_only_post boolean not null default false;

comment on column public.comm_rooms.owner_only_post is
    'Groups: true = only the room owner (and administrators) may post. Channels are always owner-only.';

create index if not exists comm_rooms_kind_idx  on public.comm_rooms (kind, is_public);
create index if not exists comm_rooms_owner_idx on public.comm_rooms (owner_email);

-- Built-in rooms (idempotent seed — never touches existing rows).
insert into public.comm_rooms (slug, name, kind, description, is_public, owner_email) values
    ('lounge',  'Lounge',    'room', 'Meet other OraCool members. Say hello.',        true, null),
    ('markets', 'Markets',   'room', 'Stocks, crypto and paper-trading talk. Educational only.', true, null),
    ('help',    'Help desk', 'room', 'Tips, questions and answers about using OraCool.', true, null)
on conflict (slug) do nothing;

-- -------------------------------------------------------------- 4. Memberships
create table if not exists public.comm_members (
    room_id   uuid not null references public.comm_rooms(id) on delete cascade,
    email     text not null,
    role      text not null default 'member' check (role in ('owner','member')),
    joined_at timestamptz not null default now(),
    primary key (room_id, email)
);

create index if not exists comm_members_email_idx on public.comm_members (email);

-- --------------------------------------------------------------- 5. Messages
create table if not exists public.comm_messages (
    id           bigint generated always as identity primary key,
    room_id      uuid not null references public.comm_rooms(id) on delete cascade,
    sender_email text not null,
    body         text not null check (char_length(body) between 1 and 800),
    kind         text not null default 'chat' check (kind in ('chat','system','mod','voice')),
    media_url    text not null default '',
    reactions    jsonb not null default '{}'::jsonb,
    created_at   timestamptz not null default now()
);

-- add the newer columns if this table was created by an older file
alter table public.comm_messages add column if not exists media_url text not null default '';
alter table public.comm_messages add column if not exists reactions jsonb not null default '{}'::jsonb;

comment on column public.comm_messages.reactions is
    'Tap reactions: map of emoji to the e-mail addresses that reacted with it.';

-- REPAIR: rebuild the kind constraint so it allows 'voice' (and everything
-- else). Older installations kept a constraint without 'voice', which
-- rejected every photo / voice-note / file DM.
alter table public.comm_messages drop constraint if exists comm_messages_kind_check;
alter table public.comm_messages add constraint comm_messages_kind_check
    check (kind in ('chat','system','mod','voice'));

create index if not exists comm_messages_room_idx   on public.comm_messages (room_id, id);
create index if not exists comm_messages_sender_idx on public.comm_messages (sender_email, id);

-- --------------------------------------------------------------- 6. Reports
create table if not exists public.comm_reports (
    id             bigint generated always as identity primary key,
    reporter_email text not null,
    subject_email  text not null,
    handle         text not null default '',
    reason         text not null default '',
    quotes         jsonb not null default '[]'::jsonb,
    counts         boolean not null default true,
    why            text not null default '',
    status         text not null default 'open'
                   check (status in ('open','upheld','unfounded','overturned')),
    case_id        bigint,
    created_at     timestamptz not null default now()
);

create index if not exists comm_reports_subject_idx  on public.comm_reports (subject_email, status);
create index if not exists comm_reports_reporter_idx on public.comm_reports (reporter_email, status);
create index if not exists comm_reports_case_idx     on public.comm_reports (case_id);

-- ------------------------------------------------------- 7. Moderator cases
create table if not exists public.comm_cases (
    id             bigint generated always as identity primary key,
    subject_email  text not null,
    handle         text not null default '',
    reporters      jsonb not null default '[]'::jsonb,
    status         text not null default 'reviewing'
                   check (status in ('reviewing','pending_admin','blocked','warned','dismissed','advisory','overturned')),
    verdict        text,
    confidence     numeric(4,3),
    category       text not null default '',
    summary        text not null default '',
    evidence       jsonb not null default '[]'::jsonb,
    action         text not null default '',
    error          text not null default '',
    decided_by     text not null default 'oracool-ai-moderator',
    opened_at      timestamptz not null default now(),
    decided_at     timestamptz
);

create index if not exists comm_cases_subject_idx on public.comm_cases (subject_email, opened_at desc);
create index if not exists comm_cases_status_idx  on public.comm_cases (status);

-- --------------------------------------------------------------- 8. Contacts
create table if not exists public.comm_contacts (
    owner_email   text not null,
    contact_email text not null,
    added_at      timestamptz not null default now(),
    primary key (owner_email, contact_email)
);

create index if not exists comm_contacts_contact_idx on public.comm_contacts (contact_email);

-- ------------------------------------------------------------ 9. Read state
-- DM unread badges: each reader's last-read message id per room.
create table if not exists public.comm_read_state (
    email        text not null,
    room_id      uuid not null references public.comm_rooms(id) on delete cascade,
    last_read_id bigint not null default 0,
    updated_at   timestamptz not null default now(),
    primary key (email, room_id)
);

-- ------------------------------------------------- 10. Room (group/channel) reports
create table if not exists public.comm_room_reports (
    id             bigint generated always as identity primary key,
    room_id        uuid not null references public.comm_rooms(id) on delete cascade,
    reporter_email text not null,
    reason         text not null default '',
    status         text not null default 'open' check (status in ('open','banned','dismissed')),
    created_at     timestamptz not null default now()
);

create index if not exists comm_room_reports_room_idx     on public.comm_room_reports (room_id, status);
create index if not exists comm_room_reports_reporter_idx on public.comm_room_reports (reporter_email);

-- ------------------------------------------------- 11. 2-player chat games
-- Games played inside a private conversation (Tic-Tac-Toe for now).
create table if not exists public.comm_games (
    id          bigint generated always as identity primary key,
    room_id     uuid not null references public.comm_rooms (id) on delete cascade,
    email_a     text not null,            -- first player (X)
    email_b     text not null,            -- second player (O)
    type        text not null default 'tictactoe',
    board       text not null default '[null,null,null,null,null,null,null,null,null]',
    turn        text,                     -- e-mail of the player to move
    status      text not null default 'playing',  -- playing | won | draw
    winner      text,                     -- e-mail of the winner (null on draw)
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create index if not exists comm_games_room_idx   on public.comm_games (room_id);
create index if not exists comm_games_status_idx on public.comm_games (status);

comment on table public.comm_games is
    'Two-player games played inside a private conversation (e.g. Tic-Tac-Toe).';

-- --------------------------------------- 13. Building coins + published sites (patch27)
-- Weekly build-coin wallet mirrored onto the user record (1,000,000 coins/week free;
-- a site build costs 10,000). Stored as JSON text so it survives redeploys.
do $$ begin
    execute 'alter table public.user_flags add column if not exists coins text';
exception when undefined_table then
    create table public.user_flags (email text primary key, coins text);
end $$;

-- One row per file of a published site. Serving flow: local disk cache first;
-- on a fresh deploy (empty disk) the server hydrates these rows back to disk.
create table if not exists public.published_sites (
    sub     text not null,
    path    text not null,
    content text not null,
    primary key (sub, path)
);
create index if not exists published_sites_sub_idx on public.published_sites (sub);

-- Registry of published sites (who owns what, hit counts, attached custom domain).
create table if not exists public.published_sites_meta (
    sub          text primary key,
    owner        text not null default '',
    name         text not null default '',
    source_slug  text not null default '',
    published_at text not null default '',
    hits         bigint not null default 0,
    custom_domain text not null default ''
);

comment on table public.published_sites is
    'Files of websites users published to <sub>.oracoolai.com (durable across redeploys).';


-- ------------------------------------- 14. The build vault — previews survive every redeploy (patch28)
-- Render's disk is ephemeral: without this, every app update erased build
-- previews ("the link does not work"). The server mirrors each build here the
-- moment it finishes and re-hydrates it on first open after a deploy.
create table if not exists public.oracool_builds (
    slug    text not null,
    path    text not null,
    content text not null,
    primary key (slug, path)
);
create index if not exists oracool_builds_slug_idx on public.oracool_builds (slug);

create table if not exists public.oracool_builds_meta (
    slug     text primary key,
    owner    text not null default '',
    name     text not null default '',
    brief    text not null default '',
    provider text not null default '',
    template text not null default '',
    t        text not null default '',
    files    text not null default '[]'
);

comment on table public.oracool_builds is
    'Files of every AI-built site, so previews and downloads survive redeploys.';
comment on table public.oracool_builds_meta is
    'Registry of build workspaces (owner, name, template, file list).';

-- ------------------------------------- 15. Server-only core tables (billing, flags, snapshots)
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
alter table public.subscribers
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
create index if not exists subscribers_email_idx on public.subscribers (email);
create index if not exists oracool_subscribers_reference_idx on public.subscribers (reference);
create index if not exists oracool_subscribers_subscribed_at_idx on public.subscribers (subscribed_at desc);

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
create unique index if not exists oracool_user_flags_email_uidx on public.user_flags (email);
create index if not exists user_flags_blocked_idx on public.user_flags (blocked);

create table if not exists public.case_store (
    k           text primary key,
    v           jsonb not null,
    updated_at  timestamptz default now()
);
alter table public.case_store
    add column if not exists v jsonb,
    add column if not exists updated_at timestamptz default now();
create unique index if not exists oracool_case_store_k_uidx on public.case_store (k);

comment on table public.subscribers is
    'OraCool backend-only subscription and payment records. Not a password store.';
comment on table public.user_flags is
    'OraCool backend-only account flags, activity and the weekly coin wallet. Not an admin-role authority.';
comment on table public.case_store is
    'OraCool backend-only JSON snapshots: main, conversations, alerts, brand_accounts.';

-- ------------------------------------- 16. Lockdown — every table, RLS on, zero policies.
-- The browser holds no Supabase keys; the OraCool server uses service_role
-- (which bypasses RLS). RLS on + no policies = total lockout for everyone else.
-- Guarded by to_regclass so the block can never fail on a partial install.
do $$
declare
    t text;
begin
    foreach t in array array[
        'comm_profiles','comm_rooms','comm_members','comm_messages',
        'comm_reports','comm_cases','comm_contacts','comm_read_state',
        'comm_room_reports','comm_games',
        'published_sites','published_sites_meta',
        'oracool_builds','oracool_builds_meta',
        'subscribers','user_flags','case_store']
    loop
        if to_regclass('public.' || t) is not null then
            execute format('alter table public.%I enable row level security', t);
            execute format('drop policy if exists "public_read" on public.%I', t);
        end if;
    end loop;
end;
$$;

-- Extra hardening for the three server-only tables: no direct grants at all,
-- service_role may operate (it also bypasses RLS). Never give service_role to
-- users or browsers.
do $$
declare
    tbl text;
    cols text;
begin
    foreach tbl in array array['subscribers', 'user_flags', 'case_store',
                               'published_sites', 'published_sites_meta',
                               'oracool_builds', 'oracool_builds_meta'] loop
        if to_regclass('public.' || tbl) is null then
            continue;
        end if;
        execute format('alter table public.%I enable row level security', tbl);
        begin
            execute format('revoke all privileges on table public.%I from public, anon, authenticated', tbl);
            select string_agg(quote_ident(column_name), ', ' order by ordinal_position)
              into cols
              from information_schema.columns
             where table_schema = 'public' and table_name = tbl;
            if cols is not null then
                execute format('revoke select (%s), insert (%s), update (%s), references (%s) on table public.%I from public, anon, authenticated',
                               cols, cols, cols, cols, tbl);
            end if;
            execute format('grant usage on schema public to service_role');
            execute format('grant select, insert, update, delete on table public.%I to service_role', tbl);
        exception when insufficient_privilege then
            raise notice 'skipped grant hardening on % (not table owner — RLS lock above still applies)', tbl;
        end;
    end loop;
end;
$$;

-- Make new tables/columns visible to the PostgREST API immediately.
notify pgrst, 'reload schema';
-- ============================================================  DONE  ========
-- Everything the app needs now lives in this ONE file. After running it:
--   * usernames, 10-digit OraCool numbers, friends-by-number, groups & channels,
--     DMs with photos/files/voice notes, profile pictures, the paid verified
--     badge, reported & bannable groups, the AI moderator, tap reactions, reply
--     quotes, group "only I can post" locking, room deletion, Tic-Tac-Toe.
--   * weekly build-coin wallets (Free 1,000,000/wk, 10,000 per site build).
--   * one-tap published websites hosted on OraCool — durable across redeploys
--     (oracoolai.com/sites/<name>/ now, <name>.oracoolai.com once wildcard DNS
--     is active) — AND every AI build workspace auto-saved to the vault, so
--     previews in chat NEVER die when the app updates (patch28).
--   * billing, account flags and case snapshots on the backend tables.
-- The running server detects all of it automatically — no code deploy needed.
-- Re-running this file is always safe: it can update columns, never deletes rows.

-- Verification — every row should exist; rls_enabled should be true for all:
select c.relname as table_name, c.relrowsecurity as rls_enabled
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
 where n.nspname = 'public' and c.relkind = 'r'
   and c.relname in ('comm_profiles','comm_rooms','comm_members','comm_messages',
                     'comm_reports','comm_cases','comm_contacts','comm_read_state',
                     'comm_room_reports','comm_games','published_sites','published_sites_meta',
                     'oracool_builds','oracool_builds_meta','subscribers','user_flags','case_store')
 order by c.relname;

-- ============================================================================
-- PART 2 · SECURITY TOOLS (patch52) + SECURITY CONSOLE (patch54)
-- ============================================================================
-- OraCool AI patch52 — Enterprise Security/OSINT toolbox support
-- Run this once in Supabase Dashboard → SQL Editor for the production project.
-- It is safe to re-run: every object uses IF NOT EXISTS / CREATE OR REPLACE / idempotent policies.

create extension if not exists pgcrypto;

-- Existing OraCool durable KV store. Patch52 uses it to publish the security-tool catalog,
-- and older patches use it for cases, builds, vault and durable Render state.
create table if not exists public.case_store (
  k text primary key,
  v jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create or replace function public.oracool_touch_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_case_store_touch on public.case_store;
create trigger trg_case_store_touch
before update on public.case_store
for each row execute function public.oracool_touch_updated_at();

alter table public.case_store enable row level security;

drop policy if exists "case_store service role all" on public.case_store;
create policy "case_store service role all"
on public.case_store
for all
to service_role
using (true)
with check (true);

-- Optional but useful: authenticated users may read public catalog rows only.
drop policy if exists "case_store authenticated read public catalog" on public.case_store;
create policy "case_store authenticated read public catalog"
on public.case_store
for select
to authenticated
using (k in ('security_tools:patch52', 'feature_matrix'));

-- Durable audit/result log for Enterprise security tools.
-- The OraCool server writes here with SUPABASE_SERVICE_KEY after each run.
create table if not exists public.security_tool_runs (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  actor_email text not null default '',
  tool text not null check (tool in ('secscan', 'sqlmap', 'pcap', 'hashaudit', 'login_audit')),
  target text not null default '',
  authorized boolean not null default false,
  status text not null default 'ok' check (status in ('ok', 'error')),
  result jsonb not null default '{}'::jsonb,
  result_sha256 text not null default '',
  source text not null default 'oracoolai.com'
);

create index if not exists security_tool_runs_actor_created_idx
  on public.security_tool_runs (lower(actor_email), created_at desc);

create index if not exists security_tool_runs_tool_created_idx
  on public.security_tool_runs (tool, created_at desc);

create index if not exists security_tool_runs_result_gin_idx
  on public.security_tool_runs using gin (result jsonb_path_ops);

create or replace function public.security_tool_runs_hash()
returns trigger
language plpgsql
as $$
begin
  if coalesce(new.result_sha256, '') = '' then
    new.result_sha256 = encode(digest(coalesce(new.result::text, ''), 'sha256'), 'hex');
  end if;
  return new;
end;
$$;

drop trigger if exists trg_security_tool_runs_hash on public.security_tool_runs;
create trigger trg_security_tool_runs_hash
before insert or update on public.security_tool_runs
for each row execute function public.security_tool_runs_hash();

alter table public.security_tool_runs enable row level security;

drop policy if exists "security tool runs service role all" on public.security_tool_runs;
create policy "security tool runs service role all"
on public.security_tool_runs
for all
to service_role
using (true)
with check (true);

-- Signed-in users can read their own run history by email claim.
-- The server still performs plan-gating; this policy is for direct Supabase reads only.
drop policy if exists "security tool runs owner read" on public.security_tool_runs;
create policy "security tool runs owner read"
on public.security_tool_runs
for select
to authenticated
using (lower(actor_email) = lower(coalesce(auth.jwt() ->> 'email', '')));

-- Compact catalog exposed to OraCool and admins.
insert into public.case_store (k, v)
values (
  'security_tools:patch52',
  jsonb_build_object(
    'build', 'patch52-sqlmap-osint',
    'tier', 'enterprise',
    'tools', jsonb_build_array(
      jsonb_build_object(
        'id', 'secscan',
        'name', 'Nmap-style port inventory',
        'route', '/api/security/nmap',
        'mode', 'safe TCP-connect inventory',
        'requires_authorization', true,
        'limits', 'public targets only; max 64 ports; no stealth/evasion/NSE scripts'
      ),
      jsonb_build_object(
        'id', 'sqlmap',
        'name', 'SQLMap-style SQL injection audit',
        'route', '/api/security/sqlmap',
        'mode', 'low-impact SQLi indicators for authorised/staging URLs',
        'requires_authorization', true,
        'limits', 'GET query parameters only in hosted mode; no crawling, table enumeration, dumping, auth bypass, time-delay or destructive payloads'
      ),
      jsonb_build_object(
        'id', 'pcap',
        'name', 'Wireshark-style pcap triage',
        'route', '/api/security/pcap',
        'mode', 'offline packet metadata analysis',
        'requires_authorization', true,
        'limits', 'classic .pcap/.cap up to hosted limit; no live sniffing or payload reconstruction'
      ),
      jsonb_build_object(
        'id', 'hashaudit',
        'name', 'John-style weak-hash audit',
        'route', '/api/security/hash',
        'mode', 'weak-password matching for owner-provided hashes',
        'requires_authorization', true,
        'limits', 'tiny built-in weak list only; no stolen dumps, masks or custom wordlists'
      ),
      jsonb_build_object(
        'id', 'login_audit',
        'name', 'Hydra-style login-defense audit',
        'route', '/api/security/login',
        'mode', 'defensive single-request login-surface review',
        'requires_authorization', true,
        'limits', 'no credential attempts, spraying or brute force'
      )
    )
  )
)
on conflict (k) do update
set v = excluded.v,
    updated_at = now();

create or replace function public.oracool_security_tool_catalog()
returns jsonb
language sql
stable
as $$
  select v from public.case_store where k = 'security_tools:patch52'
$$;

-- NOTE: the security_tool_runs_recent view is created ONCE, further below in
-- the patch54 section, with its final columns (including tool_name).
-- Creating it here first with an older column order would make the later
-- CREATE OR REPLACE fail with: cannot change name of view column "source".
drop view if exists public.security_tool_runs_recent;

grant usage on schema public to authenticated, service_role;
-- (the grant on security_tool_runs_recent lives with the CREATE below in the
-- patch54 section - the view does not exist yet at this point)
grant select on public.case_store to authenticated;
grant all on public.case_store to service_role;
grant all on public.security_tool_runs to service_role;

-- OraCool AI patch54 — Security Console + Nmap-named catalog + plan locks
-- Run once in Supabase Dashboard → SQL Editor (production project).
-- Safe to re-run: IF NOT EXISTS / CREATE OR REPLACE / ON CONFLICT UPDATE.

create extension if not exists pgcrypto;

create table if not exists public.case_store (
  k text primary key,
  v jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create table if not exists public.security_tool_runs (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  actor_email text not null default '',
  tool text not null,
  target text not null default '',
  authorized boolean not null default false,
  status text not null default 'ok' check (status in ('ok', 'error')),
  result jsonb not null default '{}'::jsonb,
  result_sha256 text not null default '',
  source text not null default 'oracoolai.com'
);

-- patch54: allow console dispatcher rows alongside the five hosted tools
alter table public.security_tool_runs
  drop constraint if exists security_tool_runs_tool_check;

alter table public.security_tool_runs
  add constraint security_tool_runs_tool_check
  check (tool in ('secscan', 'sqlmap', 'pcap', 'hashaudit', 'login_audit', 'console', 'nmap'));

create index if not exists security_tool_runs_actor_created_idx
  on public.security_tool_runs (lower(actor_email), created_at desc);

create index if not exists security_tool_runs_tool_created_idx
  on public.security_tool_runs (tool, created_at desc);

alter table public.case_store enable row level security;
alter table public.security_tool_runs enable row level security;

drop policy if exists "case_store service role all" on public.case_store;
create policy "case_store service role all"
on public.case_store for all to service_role using (true) with check (true);

drop policy if exists "security tool runs service role all" on public.security_tool_runs;
create policy "security tool runs service role all"
on public.security_tool_runs for all to service_role using (true) with check (true);

drop policy if exists "security tool runs owner read" on public.security_tool_runs;
create policy "security tool runs owner read"
on public.security_tool_runs for select to authenticated
using (lower(actor_email) = lower(coalesce(auth.jwt() ->> 'email', '')));

insert into public.case_store (k, v)
values (
  'security_tools:patch54',
  jsonb_build_object(
    'build', 'patch54-nmap-console',
    'tier', 'enterprise',
    'have_nmap', true,
    'have_sqlmap', true,
    'have_wireshark', true,
    'have_john', true,
    'have_hydra', true,
    'not_hosted', jsonb_build_array('zphisher', 'kali package installer', 'phishing kits', 'credential brute force'),
    'tools', jsonb_build_array(
      jsonb_build_object('id','nmap','internal','secscan','name','Nmap','route','/api/security/nmap',
        'how','nmap example.com','mode','live TCP-connect port/service inventory','requires_authorization', true),
      jsonb_build_object('id','sqlmap','internal','sqlmap','name','SQLMap','route','/api/security/sqlmap',
        'how','sqlmap https://example.com/item?id=1','mode','low-impact SQLi indicator audit','requires_authorization', true),
      jsonb_build_object('id','wireshark','internal','pcap','name','Wireshark','route','/api/security/pcap',
        'how','wireshark (upload .pcap)','mode','offline packet triage','requires_authorization', true),
      jsonb_build_object('id','john','internal','hashaudit','name','John the Ripper','route','/api/security/hash',
        'how','john <hash>','mode','weak-password audit of owner-provided hashes','requires_authorization', true),
      jsonb_build_object('id','hydra','internal','login_audit','name','Hydra','route','/api/security/login',
        'how','hydra https://example.com/login','mode','defensive login-surface review','requires_authorization', true),
      jsonb_build_object('id','console','internal','console','name','Security Console','route','/api/security/console',
        'how','Intel → Security Console','mode','hosted terminal for the five tools above','requires_authorization', true)
    )
  )
)
on conflict (k) do update set v = excluded.v, updated_at = now();

-- keep the older catalog key in sync so patch52 readers still work
insert into public.case_store (k, v)
select 'security_tools:patch52', v from public.case_store where k = 'security_tools:patch54'
on conflict (k) do update set v = excluded.v, updated_at = now();

insert into public.case_store (k, v)
values (
  'feature_matrix',
  jsonb_build_object(
    'build', 'patch54-nmap-console',
    'locks', jsonb_build_object(
      'free', jsonb_build_array('OSINT', 'Nmap', 'SQLMap', 'Wireshark', 'John', 'Hydra', 'Security Console'),
      'starter', jsonb_build_array('deep OSINT', 'Nmap', 'SQLMap', 'Wireshark', 'John', 'Hydra', 'Security Console'),
      'pro', jsonb_build_array('Nmap', 'SQLMap', 'Wireshark', 'John', 'Hydra', 'Security Console'),
      'ultra', jsonb_build_array('Nmap', 'SQLMap', 'Wireshark', 'John', 'Hydra', 'Security Console'),
      'enterprise', jsonb_build_array()
    )
  )
)
on conflict (k) do update set v = excluded.v, updated_at = now();

create or replace function public.oracool_security_tool_catalog()
returns jsonb
language sql
stable
as $$
  select coalesce(
    (select v from public.case_store where k = 'security_tools:patch54'),
    (select v from public.case_store where k = 'security_tools:patch52')
  );
$$;

create or replace view public.security_tool_runs_recent as
select
  id, created_at, actor_email, tool, target, authorized, status, result_sha256,
  result ->> 'risk' as risk,
  result -> 'flagged_parameters' as flagged_parameters,
  result ->> 'name' as tool_name,
  source
from public.security_tool_runs
order by created_at desc
limit 500;

grant usage on schema public to authenticated, service_role;
grant select on public.security_tool_runs_recent to authenticated, service_role;
grant select on public.case_store to authenticated;
grant all on public.case_store to service_role;
grant all on public.security_tool_runs to service_role;

-- ============================================================================
-- PART 3 · ADMIN USER BASE (patch57)
-- ============================================================================
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

-- ============================================================================
-- PART 4 · REFERRALS / ADVOCATES (patch59)
-- ============================================================================
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

-- ============================================================================
-- PART 5 · POWER ECOSYSTEM — API KEYS, AMA, SHOWCASE (patch60)
-- ============================================================================
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

-- ============================================================================
-- PART 6 · COMMUNITY BOTS — BotFather (patch64)
-- ============================================================================
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

-- ============================================================================
-- FINAL CHECK (optional) — run after the parts above; all counts can be 0+,
-- every table listed must exist with rls = true:
-- select tablename, rowsecurity from pg_tables
--  where schemaname='public'
--    and tablename in ('api_keys','comm_amas','comm_ama_questions',
--                      'comm_showcase','comm_showcase_likes','comm_bots',
--                      'referrals','user_flags')
--  order by tablename;
-- ============================================================================
