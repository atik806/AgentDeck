# AgentDeck accounts

AgentDeck signs you in with Google (via Supabase) to give the app a face in the
toolbar and to sync your setup between machines. **A signed-in account is
required** — the login window's only way forward is "Continue with Google"; the
other button quits. If the session is lost while the app is running (Sign out,
or an expired refresh token) the login window comes back, and dismissing it
closes AgentDeck.

## What signing in gives you

| | |
|---|---|
| **Toolbar chip** | Your avatar, name and plan badge, far-right on the toolbar. Click it (or the ⚙ gear) for the account dialog. |
| **Settings sync** | With *Sync settings to my account* on (default), AgentDeck mirrors a small slice of your config — working folder, recent folders, chosen agent, terminal count, layout, font size, shell, theme — to your account and pulls it back on the next sign-in. |
| **Profile** | Display name, email, and plan, read from your Supabase `profiles` row. |

The session lives at `%APPDATA%\multi-terminal\session.bin`, encrypted with
Windows DPAPI (bound to your Windows user). Delete it, or hit **Sign out** in the
account dialog, to forget the account. A cached avatar sits beside it.

## The flow

1. App start → **login window** (whenever no session is stored).
2. **Continue with Google** opens your browser to Supabase's consent page. AgentDeck
   runs a one-shot loopback HTTP server on `127.0.0.1` (port `51737`, or an
   ephemeral one if taken); Supabase redirects the browser back to it with an
   auth code; AgentDeck exchanges it (PKCE) for a session and closes the tab.
   On success the setup wizard follows.
3. **Quit AgentDeck** (or closing the window) ends the process — there is no
   signed-out mode.

## Dashboard prerequisites (one-time, done in the Supabase dashboard)

> Until these are done, **sign-in will fail** with
> *"Google sign-in isn't enabled for this AgentDeck project yet."* — that is
> expected; the Google provider ships disabled.

### 1. Enable the Google provider

1. Create an OAuth client in the [Google Cloud console](https://console.cloud.google.com/apis/credentials)
   → *Create credentials* → *OAuth client ID* → *Web application*.
2. Under **Authorized redirect URIs** add:
   `https://pxlrabmoohrfaptsotzx.supabase.co/auth/v1/callback`
3. In Supabase → **Authentication → Providers → Google**: toggle it on, paste the
   client ID and client secret, save.

### 2. Allowlist the loopback redirect

Supabase → **Authentication → URL Configuration → Redirect URLs**, add:

```
http://127.0.0.1:*
http://localhost:*
```

(The desktop app can't use a fixed port reliably, so it needs the wildcard.)

### 3. Create the tables

Apply `supabase/migrations/20260829123000_accounts.sql` — it creates
`public.profiles` and `public.user_settings` (both RLS-locked to the owner) plus
a trigger that seeds a `profiles` row on sign-up.

**With the CLI:**

```sh
supabase login
supabase link --project-ref pxlrabmoohrfaptsotzx
supabase db push
```

**Or** open the Dashboard **SQL editor**, paste the migration file, run it.

Apply `supabase/migrations/20260831150000_plan_expiry.sql` the same way — it adds
plan-expiry (see **Plan expiry** below) and needs the **pg_cron** extension
enabled once: Dashboard → **Database → Extensions → `pg_cron`** (toggle on).

## Plan expiry

A Pro grant has an **end date** and downgrades to Free on its own — no manual
clean-up.

| Column (`public.profiles`) | Meaning |
|---|---|
| `plan` | `free` / `pro` (…`paid`/`team`/`plus`). The stored plan. |
| `plan_expires_at` | `timestamptz`, or **NULL = never expires** (comp / team / lifetime). When it passes, `plan` reverts to `free`. |
| `plan_interval` | `month` / `year` / NULL. Only a hint for the admin "renew" button; the client ignores it. |

**Server side.** `public.expire_stale_plans()` sets `plan = 'free'` for every row
whose `plan_expires_at` is in the past. A **pg_cron** job (`expire-stale-plans`)
runs it every 15 minutes. Check it with `select jobname, schedule from cron.job;`.

**Client side (defence in depth).** `AccountController.plan` returns the
*effective* plan: a Pro whose `plan_expires_at` has passed reads back as `free`,
so the Free/Pro gates apply immediately even in the ≤15-min window before cron
runs, or in a long-running session. A 30-minute poll and a timer at the exact
expiry moment re-check without needing a restart. The downgrade is
non-destructive — panes already open stay; only new workspaces/panes past the
Free limits are blocked, and voice / cloud-sync / auto-update switch off.

**Granting / renewing** is done in **VibeFlow Admin** (service-role key): set
`plan` + `plan_expires_at`, or use *Grant Pro · 1 month / 1 year*. The desktop
client can no longer write to `profiles` at all (the `profiles: update own`
policy was dropped) — it only reads.

**Manual test:**

```sql
update public.profiles
   set plan = 'pro', plan_expires_at = now() - interval '1 minute'
 where id = '<test-user-id>';
select public.expire_stale_plans();                       -- returns 1
select plan from public.profiles where id = '<test-user-id>';  -- 'free'
```

## Free trial

The Free tier is a **7-day trial**. After it ends the account must be on an
active Pro plan or AgentDeck refuses to open.

- **`profiles.trial_ends_at`** (`timestamptz`, `NOT NULL`) — added by
  `20260831160000_free_trial.sql`. New signups get *signup + 7 days* (the column
  default; the `handle_new_user` trigger doesn't touch it). Existing rows were
  backfilled to *(migration time) + 7 days* so nobody was locked out on deploy.
- **Enforcement is client-side.** `entitlements.access_allowed(plan,
  trial_ends_at, plan_expires_at)` is the master gate: true on an active Pro
  plan (`plan_active`) **or** inside the trial (`trial_active`). `main.py` blocks
  before the wizard; `terminal_panel` re-checks at the exact deadline, on the
  30-min poll, and closes the app if it lapses mid-session. A dismissible
  countdown banner shows in the final 3 days. There is no server-side lockout —
  same trade-off as the Free/Pro gate.
- **Fail-open:** a missing / unfetched `trial_ends_at` never locks a user out.
- **Granting time** is done in **VibeFlow Admin** ("Trial +7d" per free user, or
  `extend_trial` on the PUT). The client can't write `profiles`, so a user can't
  extend their own trial.

**Manual test:**

```sql
update public.profiles set trial_ends_at = now() - interval '1 minute'
 where email = '<test-user>';                 -- force "trial ended"
-- launch AgentDeck as that user -> the trial gate appears instead of the app
update public.profiles set trial_ends_at = now() + interval '365 days'
 where email = '<test-user>';                 -- restore
```

## Configuration

| Knob | Where | Effect |
|---|---|---|
| `--smoke` | CLI flag | Skip the login window for this run. Build-only — `packaging/build.py`'s frozen-build check uses it. (`--no-login` is honoured only alongside `--smoke`; on its own it does nothing.) |
| `account_cloud_sync` | `config.json` / account dialog checkbox | Mirror settings to the account. |
| `account_email` | `config.json` | Last signed-in email; shown on the chip before the session loads. Set by the app. |
| `error_reporting` | `config.json` | Send crash + non-fatal error reports to your account (`public.app_errors`). Default on. Off = nothing leaves the machine; crashes still write `%APPDATA%\multi-terminal\last-error.log`. |
| `trial_banner_dismissed_on` | `config.json` | Epoch-day the free-trial countdown banner was last dismissed (so it returns once a day). Set by the app. |
| `AGENTDECK_SUPABASE_URL` | env var | Point the app at a different Supabase project. |
| `AGENTDECK_SUPABASE_KEY` | env var | Publishable key for that project. |

## Admin dashboard

The team's **VibeFlow Admin** app (a separate web project) has an *AgentDeck*
section that reads this Supabase project to list users, flip a user's `plan`
between `free` / `pro`, inspect a user's synced `user_settings`, watch GitHub
release download counts, and triage the crash feed below.

- **`public.app_errors`** — created by `supabase/migrations/20260830120000_app_errors.sql`.
  One row per crash (`kind = 'crash'`, written best-effort from `main.py`'s
  fatal handler) or reported non-fatal failure (`kind = 'error'`, via
  `AccountController.report_error`). RLS: a client may insert and read **its own**
  rows only; the admin dashboard reads everything through the **service-role
  key**, which lives only in that app's server environment — **never** in this
  repo or the shipped binary. Apply the migration the same way as the accounts
  one (`supabase db push`, or paste into the SQL editor).

`--no-wizard` skips only the wizard, not the login window.

## Security notes

- Only the Supabase **publishable** key (`sb_publishable_…`) and the project URL
  are compiled into the app. Those are designed to be public — every row is still
  gated by row-level security and a per-user JWT.
- The service-role key and the database password are **never** in the repo or the
  binary.
- Tokens on disk are DPAPI-encrypted (bound to your Windows user). On Windows, if
  DPAPI ever fails, AgentDeck **does not save the session** rather than write
  tokens in the clear — you just sign in again next launch. The plaintext-JSON
  form is only ever used off Windows, where DPAPI does not exist.
- The sign-in flow is loopback PKCE: the one-time `code_verifier` never leaves
  the process, the callback server binds `127.0.0.1` with exclusive port
  ownership, and only accepts the redirect on `/` with no `Origin` header. The
  `state` parameter is **not** ours to set on `/auth/v1/authorize` — Supabase
  owns it (it round-trips the flow-state id through Google), so we send only
  `redirect_to` + the PKCE challenge and let PKCE guard the code exchange.
