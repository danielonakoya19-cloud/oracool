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

create or replace view public.security_tool_runs_recent as
select
  id,
  created_at,
  actor_email,
  tool,
  target,
  authorized,
  status,
  result_sha256,
  result ->> 'risk' as risk,
  result -> 'flagged_parameters' as flagged_parameters,
  source
from public.security_tool_runs
order by created_at desc
limit 500;

grant usage on schema public to authenticated, service_role;
grant select on public.security_tool_runs_recent to authenticated, service_role;
grant select on public.case_store to authenticated;
grant all on public.case_store to service_role;
grant all on public.security_tool_runs to service_role;
