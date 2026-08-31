-- AgentDeck free trial: the Free tier becomes a 7-day trial.
--
-- Apply with `supabase db push` (after `supabase link --project-ref
-- pxlrabmoohrfaptsotzx`) or by pasting this file into the Dashboard SQL editor.
-- Safe to run more than once.
--
-- After the trial ends a user must be on an *active* Pro plan or the desktop
-- app refuses to open. Enforcement is client-side (like the Free/Pro gate and
-- subscription expiry); this migration only supplies the deadline.

-- ---------------------------------------------------------------------------
-- profiles.trial_ends_at
-- ---------------------------------------------------------------------------
-- NULL is never written: the column is NOT NULL with a volatile default so
--   * every existing row is backfilled to (migration time + 7 days), and
--   * the handle_new_user() trigger -- which does not name the column -- gets
--     (signup time + 7 days) for every new account, no trigger edit needed.
alter table public.profiles
    add column if not exists trial_ends_at timestamptz
    not null default (now() + interval '7 days');

-- Safety net for a re-run that had added the column nullable at some point:
update public.profiles
   set trial_ends_at = now() + interval '7 days'
 where trial_ends_at is null;

-- No pg_cron job: nothing flips server-side (Free stays Free). No RLS change:
-- the client is already SELECT-only on public.profiles (see
-- 20260831150000_plan_expiry.sql), so a user cannot extend their own trial;
-- only service_role (VibeFlow Admin) can write trial_ends_at.

-- ---------------------------------------------------------------------------
-- Manual test
-- ---------------------------------------------------------------------------
--   select email, plan, trial_ends_at from public.profiles;   -- all ~7d out
--   update public.profiles set trial_ends_at = now() - interval '1 minute'
--    where email = '<test-user>';                              -- force "ended"
--   -- launch AgentDeck as that user -> the trial gate appears instead of the app
