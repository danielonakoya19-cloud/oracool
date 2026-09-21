-- ============================================================
--   ORACOOL — PATCH 18 (Supabase)
--   Room permissions (owner-only posting & delete), message
--   reactions, file attachments, and 2-player chat games.
--
--   HOW TO RUN:
--     Supabase Dashboard -> SQL Editor -> paste this whole file
--     -> Run.   Everything is idempotent: safe to run again, and
--     safe if you already ran the Patch 16 file.
--
--   After running, the live server picks the changes up
--   automatically within about a minute.
-- ============================================================

-- ------------------------------------------------ 1. Room permissions
-- owner_only_post: on a GROUP, when true only the group creator
-- (and OraCool administrators) may post. CHANNELS are always
-- creator-only regardless of this flag.
alter table public.comm_rooms
    add column if not exists owner_only_post boolean not null default false;

comment on column public.comm_rooms.owner_only_post is
    'Groups: true = only the room owner (and administrators) may post. Channels are always owner-only.';

-- ---------------------------------------- 1b. Fix the message kind constraint
-- Patch 16 added 'voice' to the allowed kinds, but the table already existed in
-- older deployments, so the old constraint stuck and every photo/voice/file
-- DM was rejected. This repairs it (idempotent).
alter table public.comm_messages drop constraint if exists comm_messages_kind_check;
alter table public.comm_messages add constraint comm_messages_kind_check
    check (kind in ('chat','system','mod','voice'));

-- --------------------------------------------- 2. Message reactions
-- reactions jsonb: {"emoji": ["email1", "email2", ...], ...}
alter table public.comm_messages
    add column if not exists reactions jsonb not null default '{}'::jsonb;

comment on column public.comm_messages.reactions is
    'Tap reactions: map of emoji to the e-mail addresses that reacted with it.';

-- ------------------------------------------------- 3. Chat games table
-- 2-player games played inside a DM (type 'tictactoe' for now).
create table if not exists public.comm_games (
    id          bigint generated always as identity primary key,
    room_id     text not null references public.comm_rooms (id) on delete cascade,
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

create index if not exists comm_games_room_idx on public.comm_games (room_id);
create index if not exists comm_games_status_idx on public.comm_games (status);

comment on table public.comm_games is
    'Two-player games played inside a private conversation (e.g. Tic-Tac-Toe).';

-- ------------------------------------------- 4. Row Level Security (all)
-- The browser holds no Supabase keys; the OraCool server uses
-- service_role (which bypasses RLS). RLS on + no policies =
-- total lockout for everyone else.
do $$
declare
    t text;
begin
    foreach t in array array['comm_rooms','comm_messages','comm_games']
    loop
        execute format('alter table public.%I enable row level security', t);
        execute format('drop policy if exists "public_read" on public.%I', t);
    end loop;
end;
$$;

-- ============================================================  DONE  ========
-- This patch unlocks:
--   * group creators can lock posting to themselves ("only I can post")
--   * channels: only the creator (channel admin) can post
--   * creators/admins can delete their groups & channels
--   * tap reactions on messages (like WhatsApp)
--   * images, photos AND files (PDF/Office/ZIP/…) in private chats
--   * play Tic-Tac-Toe with another member right inside your chat
-- The 'avatars' and 'media' storage buckets are created automatically
-- by the server on first upload.
