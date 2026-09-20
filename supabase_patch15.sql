-- ============================================================================
-- OraCool · PATCH 15 — Community: identities, OraCool numbers, chat rooms,
-- groups & channels, direct messages, member reports, AI moderation.
--
-- HOW TO RUN (about 60 seconds):
--   1. Open your Supabase project → left menu "SQL Editor" → "New query".
--   2. Paste ALL of this file, then click "Run".
--   3. Done. Every statement below is idempotent — safe to run again any time.
--
-- What this creates:
--   * comm_profiles   — every member's public identity: unique username,
--                       unique 10-digit OraCool number, display name, bio.
--   * comm_rooms      — the 3 built-in rooms + any group or channel members
--                       create (kinds: room / group / channel / dm).
--   * comm_members    — who is in which room (owner / member).
--   * comm_messages   — every chat message, newest-first queryable.
--   * comm_reports    — member reports with credibility flags + status.
--   * comm_cases      — the AI moderator's cases and verdicts.
--   * comm_contacts   — friends a member added by OraCool number.
--   * comm_read_state — per-reader last-read message id (DM unread badges).
--
-- PRIVACY / SECURITY NOTES (by design):
--   * NO table stores an IP address. Members' IP addresses are never written
--     to this schema and are never returned to other members — only the
--     operator's own server-side logs ever see them, and no API endpoint
--     exposes one IP to another user.
--   * Row Level Security is enabled on every table with NO public policies:
--     the anon key and the browser get zero access. All reads/writes are done
--     by the OraCool server with the service_role key, which bypasses RLS.
--   * E-mails never appear in chat: the app only ever shows username + number.
-- ============================================================================

-- ---------------------------------------------------------------- 1. Numbers
-- 10-digit OraCool numbers, issued by sequence (unique by construction).
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
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);

-- Uniqueness: the same username can never be owned by two members.
create unique index if not exists comm_profiles_username_uq
    on public.comm_profiles (lower(username));
create unique index if not exists comm_profiles_number_uq
    on public.comm_profiles (oracool_number);
create index if not exists comm_profiles_display_idx
    on public.comm_profiles (lower(display_name));

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
-- kind: 'room'     = the 3 built-in public rooms (system-owned, owner null)
--       'group'    = a member-created group chat
--       'channel'  = a member-created channel
--       'dm'       = a private two-person thread
create table if not exists public.comm_rooms (
    id          uuid primary key default gen_random_uuid(),
    slug        text not null unique,
    name        text not null,
    kind        text not null default 'group' check (kind in ('room','group','channel','dm')),
    description text not null default '',
    is_public   boolean not null default true,
    owner_email text,
    created_at  timestamptz not null default now()
);

create index if not exists comm_rooms_kind_idx   on public.comm_rooms (kind, is_public);
create index if not exists comm_rooms_owner_idx  on public.comm_rooms (owner_email);

-- Built-in rooms (idempotent seed).
insert into public.comm_rooms (slug, name, kind, description, is_public, owner_email) values
    ('lounge',  'Lounge',  'room', 'Meet other OraCool members. Say hello.',                                   true, null),
    ('markets', 'Markets', 'room', 'Stocks, crypto and paper-trading talk. Educational only.',                  true, null),
    ('help',    'Help desk','room','Tips, questions and answers about using OraCool.',                          true, null)
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
    kind         text not null default 'chat' check (kind in ('chat','system','mod')),
    created_at   timestamptz not null default now()
);

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

-- ---------------------------------------------- 10. Row Level Security (all)
-- The browser holds no Supabase keys; the OraCool server uses service_role.
-- RLS on + no policies = total lockout for anon/authenticated keys.
do $$
declare
    t text;
begin
    foreach t in array array['comm_profiles','comm_rooms','comm_members','comm_messages',
                             'comm_reports','comm_cases','comm_contacts','comm_read_state']
    loop
        execute format('alter table public.%I enable row level security', t);
        -- remove any accidental public policies from earlier experiments
        execute format(
            'drop policy if exists "%s" on public.%I',
            'public_read', t);
    end loop;
end;
$$;

-- ============================================================  DONE  ========
-- After running this, the OraCool Community tab, usernames, OraCool numbers,
-- friends-by-number, groups, channels and the AI moderator are all live.
-- No code deploy is needed for the tables themselves; the running server
-- detects them automatically (it shows a short "setting up" note until the
-- first check after you run this script).
