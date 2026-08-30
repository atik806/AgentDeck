-- AgentDeck crash / error reports: one row per unhandled crash or reported
-- non-fatal failure from the desktop client.
--
-- Apply with `supabase db push` (after `supabase link --project-ref
-- pxlrabmoohrfaptsotzx`) or by pasting this file into the Dashboard SQL editor.
-- Safe to run more than once.
--
-- The client writes here with its per-user JWT (RLS: own rows only). The
-- VibeFlow Admin dashboard reads every row through the service-role key, which
-- bypasses RLS -- that key lives only in the admin app's server environment,
-- never in this repo or the shipped binary.

create table if not exists public.app_errors (
    id          bigint generated always as identity primary key,
    user_id     uuid references auth.users (id) on delete set null,
    app_version text,
    kind        text not null default 'crash',   -- 'crash' | 'error'
    phase       text,                            -- 'startup' | 'runtime'
    message     text not null,
    traceback   text,
    context     jsonb not null default '{}'::jsonb,
    os          text,
    status      text not null default 'open',    -- 'open' | 'in-progress' | 'resolved'
    created_at  timestamptz not null default now()
);

alter table public.app_errors enable row level security;

drop policy if exists "app_errors: insert own" on public.app_errors;
drop policy if exists "app_errors: read own"   on public.app_errors;

create policy "app_errors: insert own"
    on public.app_errors for insert
    with check (auth.uid() = user_id);

create policy "app_errors: read own"
    on public.app_errors for select
    using (auth.uid() = user_id);

create index if not exists app_errors_created_at_idx on public.app_errors (created_at desc);
create index if not exists app_errors_status_idx     on public.app_errors (status);
