-- ============================================================
--  ORA-COOL · PATCH 27 ADD-ON  (safe to run; fully idempotent)
--  Run this if you only want the NEW pieces: build-coin wallets +
--  published-site hosting tables. (Running supabase_complete.sql does
--  everything, including this section.)
-- ============================================================

-- Weekly build-coin wallet mirrored on the user record (free = 1,000,000
-- coins/week, a site build costs 10,000). Stored as JSON text so balances
-- survive redeploys.
do $$ begin
    execute 'alter table public.user_flags add column if not exists coins text';
exception when undefined_table then
    create table public.user_flags (email text primary key, coins text);
end $$;

-- One row per file of a published site. The server serves from local disk
-- first; after a fresh deploy (empty disk) it hydrates these rows back.
create table if not exists public.published_sites (
    sub     text not null,
    path    text not null,
    content text not null,
    primary key (sub, path)
);
create index if not exists published_sites_sub_idx on public.published_sites (sub);

-- Registry of published sites (ownership, view counter, attached custom domain).
create table if not exists public.published_sites_meta (
    sub           text primary key,
    owner         text not null default '',
    name          text not null default '',
    source_slug   text not null default '',
    published_at  text not null default '',
    hits          bigint not null default 0,
    custom_domain text not null default ''
);

comment on table public.published_sites is
    'Files of websites users published to <sub>.oracoolai.com (durable across redeploys).';

-- Lock the new tables down: only the OraCool server (service_role) can read
-- them — anon keys get nothing.
do $$
declare
    t text;
begin
    foreach t in array array['published_sites','published_sites_meta']
    loop
        execute format('alter table public.%I enable row level security', t);
        execute format('drop policy if exists "public_read" on public.%I', t);
    end loop;
end;
$$;

-- Done. The running server starts using the coins column and publishing
-- tables automatically within ~1 minute (no redeploy needed).
