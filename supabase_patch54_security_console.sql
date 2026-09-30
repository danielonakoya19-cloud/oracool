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
