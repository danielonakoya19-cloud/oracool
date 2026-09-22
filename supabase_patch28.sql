-- ============================================================================
-- ORACOOL · PATCH 28 — THE BUILD VAULT (run once, safe any time)
-- ============================================================================
-- Fixes: "my preview vanished / the website link does not work after an update".
-- Render's disk is temporary — every deploy used to erase build previews and
-- their /builds/<slug>/ links. From now on the server mirrors EVERY build into
-- these two tables the second it finishes, and re-hydrates any preview on first
-- open after a deploy. Publish stays as before (published_sites).
--
-- If you have ALREADY run the new supabase_complete.sql, you do NOT need this
-- file. If you prefer the smallest possible change to your existing project,
-- run only this file. Nothing here deletes anything; re-running is safe.
-- ============================================================================

-- One row per file of a build workspace (text files only).
create table if not exists public.oracool_builds (
    slug    text not null,
    path    text not null,
    content text not null,
    primary key (slug, path)
);
create index if not exists oracool_builds_slug_idx on public.oracool_builds (slug);

-- Registry of build workspaces (owner, name, template, file list).
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

-- Lockdown: browser roles get NO access; only the server (service_role) reads/writes.
do $$
declare
    t text;
begin
    foreach t in array array['oracool_builds','oracool_builds_meta'] loop
        if to_regclass('public.' || t) is not null then
            execute format('alter table public.%I enable row level security', t);
            execute format('drop policy if exists "public_read" on public.%I', t);
            begin
                execute format('revoke all privileges on table public.%I from public, anon, authenticated', t);
                execute format('grant usage on schema public to service_role');
                execute format('grant select, insert, update, delete on table public.%I to service_role', t);
            exception when insufficient_privilege then
                raise notice 'skipped grants on % (not table owner — RLS lock still applies)', t;
            end;
        end if;
    end loop;
end;
$$;

notify pgrst, 'reload schema';

select c.relname as table_name, c.relrowsecurity as rls_enabled
  from pg_class c
  join pg_namespace n on n.oid = c.relnamespace
 where n.nspname = 'public'
   and c.relname in ('oracool_builds','oracool_builds_meta')
 order by c.relname;
