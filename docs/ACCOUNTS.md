# AgentDeck accounts

AgentDeck can sign you in with Google (via Supabase) to give the app a face in
the toolbar and to sync your setup between machines. **It is entirely optional** —
"Continue without an account" on the login window, and everything works exactly
as before. Nothing account-related ever blocks a terminal.

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

1. App start → **login window** (only when no session is stored and
   `skip_login` is off).
2. **Continue with Google** opens your browser to Supabase's consent page. AgentDeck
   runs a one-shot loopback HTTP server on `127.0.0.1` (port `51737`, or an
   ephemeral one if taken); Supabase redirects the browser back to it with an
   auth code; AgentDeck exchanges it (PKCE) for a session and closes the tab.
3. **Continue without an account** → straight to the setup wizard, signed out.

Sign in later any time from the account chip / ⚙ gear → *Continue with Google*.

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

## Configuration

| Knob | Where | Effect |
|---|---|---|
| `--no-login` | CLI flag | Skip the login window for this run. |
| `skip_login` | `config.json` | Always skip the login window (you can still sign in from the gear). |
| `account_cloud_sync` | `config.json` / account dialog checkbox | Mirror settings to the account. |
| `account_email` | `config.json` | Last signed-in email; shown on the chip before the session loads. Set by the app. |
| `AGENTDECK_SUPABASE_URL` | env var | Point the app at a different Supabase project. |
| `AGENTDECK_SUPABASE_KEY` | env var | Publishable key for that project. |

`--no-wizard` / `--smoke` runs also skip the login window.

## Security notes

- Only the Supabase **publishable** key (`sb_publishable_…`) and the project URL
  are compiled into the app. Those are designed to be public — every row is still
  gated by row-level security and a per-user JWT.
- The service-role key and the database password are **never** in the repo or the
  binary.
- Tokens on disk are DPAPI-encrypted; a plaintext-JSON fallback is used only if
  DPAPI is unavailable (non-Windows, or a locked-down environment).
