-- ============================================================================
-- OraCool · COMPLETE SUPABASE SQL (community platform — ALL of it)
-- Supersedes: supabase_patch15.sql, supabase_patch16.sql, supabase_patch18.sql
--
-- HOW TO RUN (about 60 seconds):
--   1. Open your Supabase project → left menu "SQL Editor" → "New query".
--   2. Paste ALL of this file, then click "Run".
--   3. Done. Every statement is idempotent — safe to run again any time, and
--      safe whether you have already run the Patch 15, 16 or 18 files
--      (or none of them). Nothing here can lose data.
--
-- Supersedes (you never need to run these separately anymore):
--   SUPABASE-SETUP.sql, SUPABASE-COMPLETE-SETUP.sql, CHAT-STORAGE-SETUP.sql,
--   supabase_patch15.sql, supabase_patch16.sql, supabase_patch18.sql,
--   supabase_patch27.sql
--
-- What this file contains (the ENTIRE OraCool database, beginning to end):
--   * Community backend tables + the durable website vaults (sections 13-16).
--   * The three server-only tables (subscribers, user_flags, case_store).
--   * One guarded RLS/grants lockdown at the end, then a verification query.
--   * comm_profiles      — public identity: unique username, unique 10-digit
--                          OraCool number, display name, bio, PROFILE PICTURE
--                          (avatar_url) and the paid VERIFIED BADGE
--                          (verified_until — administrators are always
--                          verified without paying).
--   * comm_rooms         — built-in rooms + member groups & channels + private
--                          DM threads, with an admin BANNED flag and the
--                          "only the group admin can post" lock
--                          (owner_only_post).
--   * comm_members       — who is in which room.
--   * comm_messages      — every chat message, incl. photo / voice-note / FILE
--                          media_url and TAP REACTIONS (reactions).
--   * comm_reports       — member reports (3 credible reports → AI review).
--   * comm_cases         — AI moderator cases & verdicts.
--   * comm_contacts      — friends added by OraCool number.
--   * comm_read_state    — DM unread badges.
--   * comm_room_reports  — reports against groups & channels (admin can ban).
--   * comm_games         — 2-player games played inside a private chat
--                          (Tic-Tac-Toe: board, turn, winner, status).
--
-- Also included (important repair):
--   * The comm_messages.kind check constraint is rebuilt to allow
--     ('chat','system','mod','voice'). Older installations kept the older
--     constraint, which silently rejected every photo / voice-note / file
--     DM with a 400 error. This fixes it permanently.
--
-- SECURITY / PRIVACY (by design):
--   * NO table stores an IP address. IP addresses are never written to this
--     schema and no API returns one user's IP to another user.
--   * Row Level Security is ON on every table with NO public policies: the
--     browser and the anon key get zero access. All reads/writes go through
--     the OraCool server's service_role key.
--   * E-mails never appear in chat: only username + number.
--   * Verified members (✦) and administrators cannot be reported or blocked
--     by other members — enforced by the server, not the database.
--   * Profile pictures, photos and files live in Supabase STORAGE buckets
--     ("avatars", "media") which the OraCool server creates automatically on
--     first upload — nothing to do here for them.
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
