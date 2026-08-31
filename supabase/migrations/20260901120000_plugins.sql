-- AgentDeck plugins: the *metadata* of a user's plugin connections (GitHub,
-- and later GitLab / Linear / …) plus an append-only audit of every automated
-- action an agent took through one.
--
-- Apply with `supabase db push` (after `supabase link --project-ref
-- pxlrabmoohrfaptsotzx`) or by pasting this file into the Dashboard SQL editor.
-- Safe to run more than once.
--
-- IMPORTANT: no OAuth tokens are ever stored here. The GitHub token lives only
-- on the one machine, DPAPI-encrypted (github_auth.GitHubTokenStore). This table
-- only records "GitHub is connected as @x, with capabilities y" so the account
-- knows its own state across machines. See docs/PLUGINS.md §4.

-- ---------------------------------------------------------------------------
-- plugin_connections -- one row per (user, provider)
-- ---------------------------------------------------------------------------
create table if not exists public.plugin_connections (
    user_id        uuid not null references auth.users (id) on delete cascade,
    provider       text not null,                       -- 'github'
    external_login text,                                -- the linked account's login
    capabilities   text[] not null default '{}',        -- ['read','review',...]
    automation     jsonb  not null default '{}'::jsonb,  -- {capability: 'ask'|'auto'}
    connected_at   timestamptz not null default now(),
    updated_at     timestamptz not null default now(),
    primary key (user_id, provider)
);

alter table public.plugin_connections enable row level security;

drop policy if exists "plugin_connections: read own"   on public.plugin_connections;
drop policy if exists "plugin_connections: write own"  on public.plugin_connections;
drop policy if exists "plugin_connections: update own" on public.plugin_connections;
drop policy if exists "plugin_connections: delete own" on public.plugin_connections;

create policy "plugin_connections: read own"
    on public.plugin_connections for select
    using (auth.uid() = user_id);

create policy "plugin_connections: write own"
    on public.plugin_connections for insert
    with check (auth.uid() = user_id);

create policy "plugin_connections: update own"
    on public.plugin_connections for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

create policy "plugin_connections: delete own"
    on public.plugin_connections for delete
    using (auth.uid() = user_id);

-- keep updated_at current
create or replace function public.touch_plugin_connections()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists plugin_connections_touch on public.plugin_connections;
create trigger plugin_connections_touch
    before update on public.plugin_connections
    for each row execute function public.touch_plugin_connections();

-- ---------------------------------------------------------------------------
-- plugin_runs -- append-only audit of automated actions
-- ---------------------------------------------------------------------------
create table if not exists public.plugin_runs (
    id          bigint generated always as identity primary key,
    user_id     uuid references auth.users (id) on delete set null,
    provider    text not null,
    action      text not null,        -- 'review.started','review.posted','repo.created','workflow.dispatched'
    target      text,                 -- 'atik806/AgentDeck#123'
    summary     text,
    app_version text,
    created_at  timestamptz not null default now()
);

alter table public.plugin_runs enable row level security;

drop policy if exists "plugin_runs: insert own" on public.plugin_runs;
drop policy if exists "plugin_runs: read own"   on public.plugin_runs;

create policy "plugin_runs: insert own"
    on public.plugin_runs for insert
    with check (auth.uid() = user_id);

create policy "plugin_runs: read own"
    on public.plugin_runs for select
    using (auth.uid() = user_id);

create index if not exists plugin_runs_user_created_idx
    on public.plugin_runs (user_id, created_at desc);
