-- AgentDeck Skills: a user's library of reusable SKILL.md instruction docs,
-- synced across their own machines (Pro). The local
-- %APPDATA%\multi-terminal\skills.json is the offline working copy;
-- skills_cloud.SkillsCloud mirrors it here.
--
-- Apply with `supabase db push` (after `supabase link --project-ref
-- pxlrabmoohrfaptsotzx`) or by pasting this file into the Dashboard SQL editor.
-- Safe to run more than once.
--
-- The skill *body* is prose the user wrote (or an agent rewrote) -- there is
-- nothing sensitive here, but it is still strictly per-user via RLS.

-- ---------------------------------------------------------------------------
-- skills -- one row per (user, slug)
-- ---------------------------------------------------------------------------
create table if not exists public.skills (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references auth.users (id) on delete cascade,
    slug        text not null,                      -- dir/file name: ~/.claude/skills/<slug>/
    name        text not null,
    description text not null default '',           -- "when to use this"
    body        text not null default '',           -- the Markdown instructions
    enabled     boolean not null default true,
    source      text not null default 'manual',     -- 'manual' | 'upload' | 'agent'
    deleted     boolean not null default false,     -- soft-delete tombstone for sync
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    unique (user_id, slug)
);

alter table public.skills enable row level security;

drop policy if exists "skills: read own"   on public.skills;
drop policy if exists "skills: insert own" on public.skills;
drop policy if exists "skills: update own" on public.skills;
drop policy if exists "skills: delete own" on public.skills;

create policy "skills: read own"
    on public.skills for select
    using (auth.uid() = user_id);

create policy "skills: insert own"
    on public.skills for insert
    with check (auth.uid() = user_id);

create policy "skills: update own"
    on public.skills for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

create policy "skills: delete own"
    on public.skills for delete
    using (auth.uid() = user_id);

create index if not exists skills_user_updated_idx
    on public.skills (user_id, updated_at desc);
