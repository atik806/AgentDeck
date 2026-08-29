-- AgentDeck accounts: per-user profile + settings sync.
--
-- Apply with `supabase db push` (after `supabase link --project-ref
-- pxlrabmoohrfaptsotzx`) or by pasting this file into the Dashboard SQL editor.
-- Safe to run more than once.

-- ---------------------------------------------------------------------------
-- profiles: one row per auth user, created automatically on sign-up.
-- ---------------------------------------------------------------------------
create table if not exists public.profiles (
    id           uuid primary key references auth.users (id) on delete cascade,
    email        text,
    display_name text,
    avatar_url   text,
    plan         text not null default 'free',
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

alter table public.profiles enable row level security;

drop policy if exists "profiles: read own"   on public.profiles;
drop policy if exists "profiles: insert own" on public.profiles;
drop policy if exists "profiles: update own" on public.profiles;

create policy "profiles: read own"
    on public.profiles for select
    using (auth.uid() = id);

create policy "profiles: insert own"
    on public.profiles for insert
    with check (auth.uid() = id);

create policy "profiles: update own"
    on public.profiles for update
    using (auth.uid() = id)
    with check (auth.uid() = id);

-- ---------------------------------------------------------------------------
-- user_settings: the AgentDeck config subset the client mirrors to the cloud.
-- ---------------------------------------------------------------------------
create table if not exists public.user_settings (
    user_id    uuid primary key references auth.users (id) on delete cascade,
    data       jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now()
);

alter table public.user_settings enable row level security;

drop policy if exists "user_settings: own row" on public.user_settings;

create policy "user_settings: own row"
    on public.user_settings for all
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

-- ---------------------------------------------------------------------------
-- keep updated_at honest
-- ---------------------------------------------------------------------------
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

drop trigger if exists profiles_touch on public.profiles;
create trigger profiles_touch
    before update on public.profiles
    for each row execute function public.touch_updated_at();

drop trigger if exists user_settings_touch on public.user_settings;
create trigger user_settings_touch
    before update on public.user_settings
    for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- create a profile row whenever a new auth user appears (Google sign-up)
-- ---------------------------------------------------------------------------
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    insert into public.profiles (id, email, display_name, avatar_url)
    values (
        new.id,
        new.email,
        coalesce(
            new.raw_user_meta_data ->> 'full_name',
            new.raw_user_meta_data ->> 'name',
            split_part(coalesce(new.email, ''), '@', 1)
        ),
        coalesce(
            new.raw_user_meta_data ->> 'avatar_url',
            new.raw_user_meta_data ->> 'picture'
        )
    )
    on conflict (id) do nothing;
    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();
