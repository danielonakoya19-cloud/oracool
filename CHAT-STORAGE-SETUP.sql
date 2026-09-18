-- OraCool chat + encrypted connector storage.
-- Run once in your Supabase project's SQL Editor. No passwords/API keys needed here.
-- Does not delete or modify existing conversation rows.
create table if not exists public.case_store (
  k text primary key,
  v jsonb not null,
  updated_at timestamptz default now()
);
alter table public.case_store enable row level security;
revoke all on table public.case_store from anon, authenticated;
grant all on table public.case_store to service_role;
notify pgrst, 'reload schema';
