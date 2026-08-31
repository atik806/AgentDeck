-- AgentDeck plan expiry: give every Pro grant an end date and downgrade
-- automatically when it passes.
--
-- Apply with `supabase db push` (after `supabase link --project-ref
-- pxlrabmoohrfaptsotzx`) or by pasting this file into the Dashboard SQL editor.
-- Safe to run more than once.
--
-- One-time prerequisite: enable pg_cron on the hosted project
--   Dashboard -> Database -> Extensions -> pg_cron  (toggle on)
-- The `create extension` below also does it if your role is allowed to.

-- ---------------------------------------------------------------------------
-- 1. Columns on public.profiles
-- ---------------------------------------------------------------------------
-- plan_expires_at : NULL = never expires (comp / team / lifetime). A timestamp
--                   means `plan` reverts to 'free' at that moment.
-- plan_interval   : 'month' | 'year' | NULL. Only a hint for the admin "renew"
--                   button; the client never reads it.
alter table public.profiles
    add column if not exists plan_expires_at timestamptz;

alter table public.profiles
    add column if not exists plan_interval text;

do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'profiles_plan_interval_check'
    ) then
        alter table public.profiles
            add constraint profiles_plan_interval_check
            check (plan_interval is null or plan_interval in ('month', 'year'));
    end if;
end
$$;

-- ---------------------------------------------------------------------------
-- 2. Close the self-upgrade hole
-- ---------------------------------------------------------------------------
-- `profiles: update own` let any signed-in user PATCH their own row -- including
-- `plan` and now `plan_expires_at`. The desktop client never writes to
-- `profiles` (it only ever selects from it; settings writes go to
-- `user_settings`), so drop the client's UPDATE entirely. The VibeFlow Admin
-- app uses the service-role key, which bypasses RLS and is unaffected.
drop policy if exists "profiles: update own" on public.profiles;
revoke update on public.profiles from authenticated;
-- Keep the admin (service-role) path explicit; it bypasses RLS anyway.
grant update on public.profiles to service_role;

-- ---------------------------------------------------------------------------
-- 3. The downgrade function
-- ---------------------------------------------------------------------------
-- security definer + owned by the migration role so it can update rows past
-- RLS. Returns how many profiles it downgraded (handy when run by hand).
create or replace function public.expire_stale_plans()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    n integer;
begin
    update public.profiles
       set plan = 'free'
     where plan <> 'free'
       and plan_expires_at is not null
       and plan_expires_at < now();
    get diagnostics n = row_count;
    return n;
end;
$$;

revoke all on function public.expire_stale_plans() from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- 4. Run it every 15 minutes with pg_cron
-- ---------------------------------------------------------------------------
-- pg_cron is not relocatable; Supabase installs it into the `cron` schema.
-- If your role can't create it, enable it in the Dashboard first (see header).
create extension if not exists pg_cron;

select cron.unschedule('expire-stale-plans')
 where exists (select 1 from cron.job where jobname = 'expire-stale-plans');

select cron.schedule(
    'expire-stale-plans',
    '*/15 * * * *',
    $$ select public.expire_stale_plans(); $$
);

-- ---------------------------------------------------------------------------
-- Manual test
-- ---------------------------------------------------------------------------
--   update public.profiles
--      set plan = 'pro', plan_expires_at = now() - interval '1 minute'
--    where id = '<some-test-user-id>';
--   select public.expire_stale_plans();          -- returns 1
--   select plan from public.profiles where id = '<same-id>';   -- 'free'
--   select jobname, schedule from cron.job;      -- shows expire-stale-plans
