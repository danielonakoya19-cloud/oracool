-- ============================================================================
-- OraCool · PATCH 16 — COMPLETE community schema (supersedes the Patch 15 file)
--
-- HOW TO RUN (about 60 seconds):
--   1. Supabase dashboard → left menu "SQL Editor" → "New query".
--   2. Paste ALL of this file, then click "Run".
--   3. Done. Every statement is idempotent — safe to run again any time,
--      and safe to run if you already ran the older Patch 15 file.
--
-- What this file contains (the WHOLE community platform):
--   * comm_profiles      — public identity: unique username, unique 10-digit
--                          OraCool number, display name, bio, PROFILE PICTURE
--                          (avatar_url) and the paid VERIFIED BADGE
--                          (verified_until — administrators are always
--                          verified without paying).
--   * comm_rooms         — built-in rooms + member groups & channels + private
--                          DM threads, with an admin BANNED flag.
--   * comm_members       — who is in which room.
--   * comm_messages      — every chat message, incl. VOICE NOTE media_url.
--   * comm_reports       — member reports (3 credible reports → AI review).
--   * comm_cases         — AI moderator cases & verdicts.
--   * comm_contacts      — friends added by OraCool number.
--   * comm_read_state    — DM unread badges.
--   * comm_room_reports  — reports against groups & channels (admin can ban).
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
--   * Profile pictures & voice notes live in Supabase STORAGE buckets
--     ("avatars", "media") which the OraCool server creates automatically on
--     first use — nothing to do here for them.
-- ============================================================================

-- ---------------------------------------------------------------- 1. Numbers
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

-- add the new columns if this table was created by the older Patch 15 file
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
    created_at  timestamptz not null default now()
);

alter table public.comm_rooms add column if not exists banned     boolean not null default false;
alter table public.comm_rooms add column if not exists ban_reason text not null default '';

create index if not exists comm_rooms_kind_idx  on public.comm_rooms (kind, is_public);
create index if not exists comm_rooms_owner_idx on public.comm_rooms (owner_email);

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
    created_at   timestamptz not null default now()
);

alter table public.comm_messages add column if not exists media_url text not null default '';

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

-- ---------------------------------------------- 11. Row Level Security (all)
-- The browser holds no Supabase keys; the OraCool server uses service_role
-- (which bypasses RLS). RLS on + no policies = total lockout for everyone else.
do $$
declare
    t text;
begin
    foreach t in array array['comm_profiles','comm_rooms','comm_members','comm_messages',
                             'comm_reports','comm_cases','comm_contacts','comm_read_state',
                             'comm_room_reports']
    loop
        execute format('alter table public.%I enable row level security', t);
        execute format('drop policy if exists "public_read" on public.%I', t);
    end loop;
end;
$$;

-- ============================================================  DONE  ========
-- After running this, everything is live: usernames, OraCool numbers,
-- friends-by-number, groups & channels, DMs, voice notes, profile pictures,
-- the paid verified badge, reported & bannable groups, and the AI moderator.
-- The running server detects the changes automatically (~1 minute).
