# AgentDeck plugins

> **Status: P0–P2 working end-to-end (2026-09-01).** Device-flow connect, token
> vault, user-scope MCP injection into `~/.claude.json`, and the GitHub-review
> flow are implemented, unit-tested, and **verified live**: a Claude Code pane
> called `get_me` / `list_pull_requests` through the hosted MCP endpoint
> (`api.githubcopilot.com/mcp/`) with the GitHub App user token — no auth error.
> **Not done:** Activity-log viewer, local `github-mcp-server` fallback, agents
> other than Claude Code, the audit-table migration is not yet applied. See
> **§11**.

## 1. What a plugin is

A **plugin** connects AgentDeck to an outside service the user already has an
account with, and turns that connection into **tools the coding agent in a pane
can call directly** — no copy-pasting tokens, no "run this `gh` command for me".

For GitHub that means the agent can, on its own:

- read a repo, a branch, a PR diff, issues, checks;
- **review a pull request** — fetch the diff, analyse it, post inline comments
  and a summary review (the v1 capability);
- create a repo, open/merge PRs, push commits;
- **dispatch and inspect GitHub Actions** — trigger a `workflow_dispatch`, watch
  a run, pull failing-job logs;
- triage issues (label, comment, close, assign).

The user connects once from the **Plugins** page; every workspace they point at
one of their GitHub repos then gets those tools wired into its agent.

### How the agent actually gets the tools

Agents in AgentDeck are external CLIs (`claude`, `codex`, …) that we launch in a
ConPTY pane — we don't control their tool surface at runtime. We already reach
*into* an agent's config once (`agents.pretrust_folder` writes
`~/.claude.json`). The plugin system does the same, deliberately:

**AgentDeck writes a GitHub MCP server entry into the agent's project MCP
config**, authenticated with the user's connected GitHub token, scoped to the
workspace folder. When the agent starts in that folder it picks up the MCP
server and its GitHub toolset.

- **Transport (v1): GitHub's hosted remote MCP server**,
  `https://api.githubcopilot.com/mcp/`, with the user token as a bearer header.
  No local binary, no Docker, GitHub maintains it. Toolsets (`repos`, `issues`,
  `pull_requests`, `actions`, `code_security`, …) are switchable per connection.
- **Fallback:** the official local `github-mcp-server` binary (downloaded and
  cached like Velopack does its assets) for users who can't reach the remote
  endpoint or want everything on-box.
- **Agent coverage (v1): Claude Code only** — it's the default agent. The
  server goes into the **root `mcpServers` of `~/.claude.json`** (Claude Code's
  *user* scope), *not* a `<folder>/.mcp.json` and *not* a per-project entry — so
  a pane has the GitHub tools whatever folder it's `cd`'d to. User-scope servers
  are trusted automatically (no `/mcp` prompt), the token never lands in a repo,
  and there's no clash with `pretrust_folder`. An older build wrote a project
  `.mcp.json`; `github_mcp.cleanup_legacy_mcp_json` deletes any it left and
  `remove()` sweeps stale per-project entries. The injector (`github_mcp.py`) is
  pluggable so Codex (`~/.codex/config.toml`), Gemini (`.gemini/settings.json`),
  Copilot CLI, etc. can be added one at a time. Non-supported agents still get
  the connection + the "post a review"
  button; they just don't get in-agent tools yet.

## 2. The Plugins page

Routing already exists: the sidebar's **Plugins** nav item →
`terminal_panel._show_plugins` → `PluginsPanel`. We replace the empty state with
a real two-level view. (Visual spec follows the mockup the user provided; the
structure below is what the code needs to support.)

### 2a. Catalog view

```
┌─────────────────────────────────────────────────────────────┐
│  Plugins                                     [ search…  ]    │
│  Connect AgentDeck to the tools your agents work in.         │
│                                                             │
│  [ All ]  [ Version control ]  [ Project mgmt ]  [ CI/CD ]   │
│                                                             │
│  ┌───────────────┐ ┌───────────────┐ ┌───────────────┐       │
│  │  GitHub       │ │  GitLab       │ │  Linear       │       │
│  │  Version ctrl │ │  Version ctrl │ │  Project mgmt │       │
│  │  Review PRs,  │ │               │ │               │       │
│  │  run actions… │ │  Coming soon  │ │  Coming soon  │       │
│  │ ●  Connected  │ │               │ │               │       │
│  │  [ Manage ]   │ │  [   —   ]    │ │  [   —   ]    │       │
│  └───────────────┘ └───────────────┘ └───────────────┘       │
└─────────────────────────────────────────────────────────────┘
```

Each **card**: icon, name, category tag, one-line description, a status pill
(`Not connected` / `Connected as @login` / `Needs attention`), and one primary
button (`Connect` → `Manage`). Three live cards — **GitHub**, **Vercel** and
**Jira** (the last two thin — see §12 / §13) — with the rest rendered disabled as
`Coming soon` (GitLab, Bitbucket, Linear, Sentry, Netlify).

### 2b. Detail / manage view (click a card)

- **Connection** — connect / disconnect, which GitHub account, when connected,
  token expiry, "Reconnect" if the token is stale.
- **Repositories** — the repos the AgentDeck GitHub App is installed on
  (`Add repositories` deep-links to the GitHub install-settings page).
- **Capabilities** — a checklist the user opts into, each mapping to an MCP
  toolset + token scope tier (see §5):
  - ☑ Read code & PRs *(always on when connected)*
  - ☑ Review pull requests — post comments & reviews
  - ☐ Manage issues — label, comment, close
  - ☐ Write code — create branches, commit, open PRs
  - ☐ Run GitHub Actions — dispatch workflows, read logs
  - ☐ Admin — create / delete repos
- **Automation mode** — per capability: `Ask first` (default) vs `Autonomous`.
- **Activity** — the audit log for this plugin (§4), newest first.

### 2c. Gating

Plugins is a **Pro** feature, consistent with cloud sync / per-workspace config
(`entitlements.py`). Free users see the catalog and the GitHub detail page as an
upsell; **Connect** is disabled with the standard `upgrade_hint`. Add
`entitlements.plugins_enabled(plan)` and `entitlements.github_automation_enabled(plan)`.

## 3. Connecting GitHub

Use a dedicated **AgentDeck GitHub App** (not a plain OAuth App) with the
**OAuth device flow**:

- A GitHub App gives **per-repository** permissions the user chooses at install
  time, fine-grained permission scopes, and a 15k/h rate limit.
- **Device flow** needs no client secret in the shipped binary and no loopback
  server — simpler than the Google flow we already run. User sees a code, opens
  `https://github.com/login/device`, we poll `/login/oauth/access_token` until
  authorised.
- User-to-server tokens expire (~8 h) with a refresh token; `github_auth.py`
  refreshes them the way `supabase_auth.refresh` does.

### Token storage

- **Local only**, `%APPDATA%\multi-terminal\github.bin`, DPAPI-encrypted — reuse
  `supabase_auth.SessionStore`'s exact pattern (magic bytes, atomic replace,
  refuse-to-write-plaintext-on-Windows).
- **Never synced to the cloud.** Re-auth per machine.
- What *does* go to Supabase is **connection metadata** (see §4) so the account
  knows "GitHub is connected as @x with capabilities y" across machines, and the
  profile chip / other clients can reflect it.

### The flow

1. Plugins → GitHub card → **Connect** (Pro only).
2. `github_controller` starts the device flow on a worker thread (mirror
   `account.AccountController` — signals up top, `_Worker` QThread, busy state).
3. Dialog shows the user code + a button that opens the browser; we poll.
4. On success: store the token locally, write the connection row to Supabase,
   emit `connected(login)`, refresh the card.
5. First connect also walks the user through **installing the GitHub App** on
   the repos they want (GitHub hosts that screen).

### Dashboard prerequisites (one-time)

Documented like ACCOUNTS.md §"Dashboard prerequisites":

1. Register the **AgentDeck** GitHub App (github.com/settings/apps) — enable
   *Device flow*, set the permission list (§5), no callback URL needed for
   device flow, note the **App ID** and **Client ID** (public, ship in the
   binary; there is no secret with device flow).
2. Publish the App (public) so any user can install it.
3. Create the Supabase tables (§4) — migration
   `supabase/migrations/2026090XXXXXXX_plugins.sql`.

## 4. Data model

### Local

`%APPDATA%\multi-terminal\plugins.json` — non-secret per-machine state:

```json
{
  "github": {
    "login": "atik806",
    "connected_at": "2026-09-01T10:00:00Z",
    "capabilities": ["read", "review"],
    "automation": { "review": "ask" },
    "transport": "remote"
  }
}
```

Token lives separately in `github.bin` (DPAPI). `plugins.json` is safe to read
without decryption for rendering the catalog.

### Supabase

```sql
-- plugin_connections: one row per (user, provider). Metadata only — NO TOKENS.
create table public.plugin_connections (
  user_id       uuid references auth.users(id) on delete cascade,
  provider      text not null,               -- 'github'
  external_login text,
  capabilities  text[]  not null default '{}',
  automation    jsonb   not null default '{}',
  connected_at  timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  primary key (user_id, provider)
);

-- plugin_runs: append-only audit of every automated action an agent took.
create table public.plugin_runs (
  id          bigint generated always as identity primary key,
  user_id     uuid references auth.users(id) on delete cascade,
  provider    text not null,
  action      text not null,        -- 'review.posted', 'repo.created', 'workflow.dispatched'
  target      text,                 -- 'atik806/AgentDeck#123'
  summary     text,
  app_version text,
  created_at  timestamptz not null default now()
);
```

RLS: `user_id = auth.uid()` for select/insert on both; no update/delete for
clients on `plugin_runs`. Same style as `20260830120000_app_errors.sql`.

## 5. Capability → scope / toolset map

| Capability | GitHub App permissions | MCP toolset(s) | Default |
|---|---|---|---|
| Read code & PRs | `contents:read`, `pull_requests:read`, `metadata:read` | `repos`, `pull_requests` (read) | on |
| Review pull requests | `pull_requests:write` | `pull_requests` | **on (v1)** |
| Manage issues | `issues:write` | `issues` | off |
| Write code | `contents:write` | `repos` (write) | off |
| Run GitHub Actions | `actions:write` | `actions` | off |
| Admin (create/delete repos) | `administration:write` | `repos` (admin) | off |

The injected MCP config only enables toolsets for capabilities the user ticked,
so an agent physically cannot call `run_workflow` unless "Run GitHub Actions" is
on.

## 6. GitHub review — the first capability

The headline v1 feature. Two ways in, one engine.

### Engine

`github_mcp.build_review_brief(repo, pr, options)` + `review_startup_command()`
produce the review pane's task. `github_mcp.inject(folder, token, connection)`
writes the scoped `github` server into the **root `mcpServers`** of
`~/.claude.json` (`folder` is used only for legacy `.mcp.json` cleanup) — removed
by `remove()` on disconnect; idempotent; declines a `github` server the user
configured themselves. `github_controller.ensure_wired()` is called on connect,
on startup-if-connected, and after a capability change.

### Entry point A — "Review a pull request" action

A command (palette + a button on the GitHub detail page):

1. `github_review_dialog` — pick repo (from the installed list), pick PR (open
   PRs listed via the API), or paste a PR URL. Options: *post to GitHub* vs
   *just tell me here*; *comment / request changes / approve* (default
   **comment**); focus areas (bugs / security / style / tests).
2. AgentDeck spawns a **new pane** in the current workspace (or a dedicated
   "Reviews" workspace), agent = the review agent (default Claude Code),
   `cwd` = a shallow clone or the existing local checkout if the folder already
   is that repo.
3. It injects the GitHub MCP server (toolset `pull_requests`, review capability)
   and sends the review prompt as the startup command:

   > Review pull request #123 in `atik806/AgentDeck`. Use the GitHub tools to
   > fetch the PR, its diff and its checks. Assess correctness, security, and
   > and test coverage. Post a single review with inline comments on the
   > specific lines, then a short summary. Do **not** approve or request
   > changes — leave it as a comment. Stop and show me the review before
   > posting if anything looks destructive.

4. Output streams in the pane. If *post to GitHub* was on, the agent posts via
   MCP; AgentDeck writes a `plugin_runs` row (`review.posted`,
   `atik806/AgentDeck#123`).

### Entry point B — the agent asks on its own

Once a workspace folder is a connected repo and "Review pull requests" is on,
any agent in that workspace already has the tools. A user typing "review the
open PRs" in the pane just works — no AgentDeck dialog involved.

### Guardrails

- Default review event is **COMMENT**; `approve` / `request_changes` require the
  user to have picked it in the dialog *and* confirm.
- `Ask first` automation mode: the agent is told to print the review and wait;
  AgentDeck surfaces a **Post to GitHub** button in the pane header.
- Every write action → a `plugin_runs` row and a toast.
- A hard cap on repos (the App installation list) means an agent can't touch
  anything the user didn't explicitly add.

## 7. New modules & files

Qt-free core (unit-tested headless, same rule as `supabase_auth.py` /
`agents.py` / `entitlements.py`):

| File | Role |
|---|---|
| `windows_launcher/github_auth.py` | Device-flow OAuth, token refresh, `GitHubTokenStore` (DPAPI). |
| `windows_launcher/github_api.py` | Thin `requests` wrapper — list installations/repos, list PRs, whoami. |
| `windows_launcher/github_mcp.py` | Build/inject/remove the MCP server entry per agent; build review prompts. Pluggable per-agent writers. |
| `windows_launcher/plugin_store.py` | `plugins.json` read/write + Supabase `plugin_connections` / `plugin_runs` sync. |

Qt layer:

| File | Role |
|---|---|
| `windows_launcher/github_controller.py` | `QObject` bridge — mirrors `account.AccountController` (signals, `_Worker`, busy). |
| `windows_launcher/plugins_panel.py` | Rebuilt: catalog + detail views. Keep `plugin_icon`. |
| `windows_launcher/plugin_card.py` | The catalog card widget. |
| `windows_launcher/github_review_dialog.py` | Repo/PR picker + review options. |

Migrations:

| File | Role |
|---|---|
| `supabase/migrations/2026090XXXXXXX_plugins.sql` | `plugin_connections`, `plugin_runs`, RLS. |

Tests: extend `test_plugins_panel.py` (catalog render, card states, Pro gate);
new `test_github_auth.py` (device-flow state machine w/ mocked HTTP, token vault
round-trip), `test_github_mcp.py` (injector writes/removes/merges `.mcp.json`,
idempotent, prompt builder), `test_plugin_store.py`.

## 8. Integration points in existing code

- **`terminal_panel.py`** — construct `GitHubController` alongside
  `AccountController`; pass it to `PluginsPanel`; add the "Review a pull
  request" command; when a workspace/pane spawns in a folder that is a connected
  repo, call `github_mcp.inject(...)` before the startup command fires (next to
  the existing `pretrust_folder` call near line 623).
- **`account.py`** — nothing goes in `CLOUD_KEYS` (token is local). Optionally
  read `github_login` off the profile row for the chip.
- **`entitlements.py`** — add `plugins_enabled` / `github_automation_enabled`;
  extend the Free/Pro table + `docs`/pricing.
- **`workspace_sidebar.py`** — the Plugins nav item can grow a small green dot
  when a plugin is mid-action, reusing the activity-dot the workspace rows have.
- **`config.py`** — no schema change; `plugins.json` is a sibling file.

## 9. Rollout phases

| Phase | Ships | Depends on |
|---|---|---|
| **P0 — Catalog shell** | Rebuilt `plugins_panel.py`: catalog + detail views, GitHub card, Pro gate, "Coming soon" others. No connection yet. | — |
| **P1 — Connect GitHub** | GitHub App + device flow, `github_auth.py`, token vault, `plugin_store.py`, `plugin_connections` migration, connect/disconnect UI, repo list. | GitHub App registered, migration applied |
| **P2 — GitHub review MVP** | `github_mcp.py` (Claude Code), `github_review_dialog.py`, spawn review pane, remote GitHub MCP, results in pane, manual "Post to GitHub" + confirm, `plugin_runs` audit. | P1 |
| **P3 — Capabilities & audit** | Capability checklist → scoped toolsets, `Ask first` / `Autonomous` modes, Activity log viewer, local `github-mcp-server` fallback. | P2 |
| **P4 — More automations & agents** | Create repo, open/merge PRs, dispatch & inspect Actions, issue triage. Codex + Gemini MCP writers. | P3 |
| **P5 — More providers** | GitLab / Linear / … using the same `plugin_store` + capability model. | P4 |

## 10. Open questions

1. **Clone vs. reuse local checkout** for a review pane — if the workspace
   folder already is the repo, review in place on a scratch worktree; otherwise
   shallow-clone to a temp dir and clean up after.
2. **Headless review** (`claude -p "<prompt>"`) as an option so a review can run
   without a visible pane and just drop its summary into the Activity log.
3. **Remote MCP reachability** — confirm `api.githubcopilot.com/mcp/` works for
   users without a Copilot subscription (docs suggest yes for the App-token
   path); if not, local binary becomes P2 not P3.
4. **Token-scope UX** — GitHub App permissions are fixed at registration; the
   "capabilities" checklist then only *narrows* what MCP exposes, it can't grant
   more than the App has. Decide the App's full permission set up front (lean
   superset of §5) and gate purely client-side.

## 11. Implementation status

### Built & tested (`windows_launcher/`)

| Module | What it does | Tests |
|---|---|---|
| `secret_store.py` | `EncryptedJsonStore` — DPAPI-encrypted JSON at rest, Windows-bound, refuses plaintext on Windows. | `test_github_auth.py` §5–6 |
| `github_auth.py` | OAuth **device flow** (`DeviceFlow.start`/`poll_once`/`run`), token refresh, `GitHubTokenStore` (`github.bin`, never cloud-synced). | `test_github_auth.py` |
| `plugin_store.py` | `plugins.json` metadata, the capability model (`CAPABILITIES`, `normalise_capabilities`, `toolsets_for`), `PluginConnection`. | `test_plugin_store.py` |
| `github_mcp.py` | Injects/removes the `github` server in `~/.claude.json` `projects[…].mcpServers` (idempotent, path-form-aware, refuses hand-rolled servers), `remove_all`, `cleanup_legacy_mcp_json`, `_git_exclude`, review-brief + startup-command builders. | `test_github_mcp.py` |
| `github_api.py` | `whoami`, `list_repos` (App installations), `list_open_prs`, `parse_pr_url`. | (via controller test) |
| `github_controller.py` | Qt bridge — connect/disconnect lifecycle, capability edits, `ensure_wired`/`unwire_all`, `_valid_token_blocking`, best-effort Supabase mirror + `log_run`. | `test_github_controller.py` |
| `plugins_panel.py` | Rebuilt: catalog grid + GitHub detail (connect, device-code box, capability checklist + automation combos, repo list, "Review a pull request"). | `test_plugins_panel.py` §4 |
| `github_review_dialog.py` | Repo/PR picker + focus/post options → `review_ready` payload. | (smoke) |
| `entitlements.py` | `plugins_enabled` / `github_automation_enabled` (Pro-gated). | `test_entitlements.py` §3 |
| `terminal_panel.py` | Builds `GitHubController`; wires the panel; `_wire_github_for` injects the server before a workspace's panes start **and** on `github.connected` (with a "restart the agent (↻)" status nudge); `_start_github_review` spawns a review workspace; teardown unwires + `remove_all` + shuts down. | `test_panel*.py` (unchanged, still green) |
| `supabase/migrations/20260901120000_plugins.sql` | `plugin_connections` + `plugin_runs` with RLS. | — |

### To finish P1–P2 in production

1. **GitHub App registered** — client id `Iv23liY7p5rRtAOm6mtc` is baked into
   `github_auth._DEFAULT_CLIENT_ID` (override with `AGENTDECK_GITHUB_CLIENT_ID`).
   Device flow verified against the live endpoint.
2. **Apply the migration** to the hosted project (`supabase db push`).
3. Confirm the App's permission set matches §5 and set the repos-install UX copy.
4. ~~Verify the hosted MCP endpoint serves the GitHub App user token~~ —
   **done 2026-09-01**, works (a local fallback is now a nice-to-have, not a
   blocker).

### Known: agent must be (re)started after connecting

`.claude.json` is read by `claude` at launch. Connecting GitHub while a `claude`
pane is already running does nothing for that pane — the user restarts the agent
(pane header `↻`) or opens a new workspace. `_on_github_connected` re-injects the
working folder and shows a status-bar nudge saying so.

### Not started

Activity-log viewer (P3), `Ask first` vs `Autonomous` enforcement for ad-hoc
actions (the combo persists but only the review flow's "post" checkbox honours
it), local `github-mcp-server` fallback (P3), Codex/Gemini writers (P4), other
providers (P5). Also: the token written into `.claude.json` is static, so a
multi-hour agent session can outlive it (~8 h) — a new workspace re-injects a
fresh one, a long-running pane does not.

## 12. Vercel plugin (thin) — shipped v0.9.0 (2026-09-01)

The second live card. **Deliberately much thinner than GitHub** because Vercel's
official MCP server works differently.

### Why it's thin

Vercel's MCP server is **hosted and OAuth-only** — `https://mcp.vercel.com`,
implementing the MCP Authorization spec (PKCE + Dynamic Client Registration).
It does **not** accept an API bearer token, and there is no official local
binary. Claude Code is an approved client and does the OAuth itself: the user
runs `/mcp` in a pane once and **Claude Code stores and owns those credentials**.

So AgentDeck never touches a Vercel token. "Connecting" the plugin means: record
it in `plugins.json`, drop a **tokenless** server entry into `~/.claude.json`,
and mirror the metadata row. Authorising happens in the pane. Status is
*"Enabled"*, not *"Connected as @user"* — there's no identity to show and no way
to verify the OAuth completed (Claude Code's credential store is undocumented and
version-specific, so we don't probe it).

### The injected block (root `mcpServers.vercel` of `~/.claude.json`)

```json
{ "type": "http", "url": "https://mcp.vercel.com", "x-agentdeck-managed": true }
```

No `headers`, no capability/toolset filtering (Vercel's OAuth consent screen is
where scope is chosen). `x-agentdeck-managed` gates removal exactly as for GitHub.

### Modules

| Module | What it does | Tests |
|---|---|---|
| `vercel_mcp.py` | `inject()` / `remove()` / `mcp_server_config()` — copies github_mcp.py's `_claude_config_path` / `_load_json` / `_atomic_write_json` / `_strip_managed` verbatim (not a shared module — protects shipped v0.8.0). No `token`/`folder`/`connection` params. | `test_vercel_mcp.py` |
| `vercel_controller.py` | `VercelController(QObject)` — `start_connect` (put + `ensure_wired` + `_mirror_up` + emit), `disconnect`, `ensure_wired` (Claude-Code-only), `_mirror_delete`. No device flow, no token vault, no capability model, no `log_run`. Startup `ensure_wired` staggered `singleShot(250)` behind GitHub's `singleShot(0)`. | `test_vercel_controller.py` |
| `plugin_store.py` | Added `VERCEL = "vercel"`. `PluginConnection` unchanged — the vercel row's `capabilities`/`automation` are unused noise. | `test_plugin_store.py` §6 |
| `plugins_panel.py` | `_VercelDetail` (inline, mirrors `_GitHubDetail` minus device-code/caps/repos), `_vercel_icon` (drawn triangle), `_PluginCard.set_toggle_status` ("ENABLED" / "NOT ENABLED"), 3rd stack page, `_open_detail`/`_sync_cards` branches. | `test_plugins_panel.py` §5 |
| `terminal_panel.py` | Builds `VercelController`, passes `vercel=` to the panel, `_wire_vercel_for`, `_on_vercel_connected/_disconnected` status nudges, teardown. | `test_panel*.py` (unchanged) |

### Data model

Reuses `public.plugin_connections` with `provider='vercel'` — the table is
provider-generic (PK `(user_id, provider)`, RLS `auth.uid() = user_id`).
**No migration.** `plugin_runs` is unused by Vercel in v1.

### Entitlements

Reuses `entitlements.plugins_enabled(plan)` (Pro gate) unchanged. Free users see
the live card; Connect is disabled and labelled "(Pro)".

### Known: same "(re)start the agent" caveat as GitHub

`claude` reads `.claude.json` at launch. After enabling Vercel the user restarts
the agent (`↻`) **and then runs `/mcp`** to authorise. `_on_vercel_connected`
re-injects and shows a status-bar nudge saying so.

## 13. Jira plugin (thin) — shipped v0.9.0 (2026-09-01)

The third live card. **A near-exact clone of the Vercel plugin** (§12) — the
`_mcp` / `_controller` modules are byte-identical bar the constants and copy.

### Why it's thin

The plugin talks to Atlassian's official **Rovo Remote MCP Server** (GA Feb
2026) — hosted, **Cloud-only**, **OAuth 2.1** at
`https://mcp.atlassian.com/v1/mcp/authv2`, transport `type: "http"`. One
connection covers Jira, Confluence, Jira Service Management, Bitbucket and
Compass. No bearer token in v1 (an API-token path exists but needs an org admin
to enable it and exposes fewer tools); no local binary. Claude Code does the
OAuth via `/mcp` and owns the credentials. So AgentDeck never touches an
Atlassian token — status is *"Enabled"*, not *"Connected as @user"*.

### The injected block (root `mcpServers.atlassian` of `~/.claude.json`)

```json
{ "type": "http", "url": "https://mcp.atlassian.com/v1/mcp/authv2", "x-agentdeck-managed": true }
```

The MCP server is named **`atlassian`** (Atlassian's own convention; what `/mcp`
shows). The AgentDeck-side **provider key stays `jira`** (`_CATALOG` key,
`plugin_store.JIRA`, the Supabase `plugin_connections.provider` value). The URL is
the single constant `jira_mcp.REMOTE_MCP_URL` — Atlassian docs also reference
`https://mcp.atlassian.com/v2/mcp`; switch the constant if `/mcp` rejects it.
(`.../v1/sse` is deprecated since 2026-06-30.)

### Modules

| Module | Notes |
|---|---|
| `jira_mcp.py` | Copy of `vercel_mcp.py`; `_SERVER_NAME = "atlassian"`, the Atlassian URL. `inject`/`remove`/`_strip_managed` are transport-agnostic — they only key on the name + `x-agentdeck-managed`. |
| `jira_controller.py` | Copy of `vercel_controller.py` → `JiraController`; `provider="jira"` in the Supabase mirror. Startup `ensure_wired` staggered `singleShot(400)` (behind GitHub's `0` and Vercel's `250`). |
| `plugin_store.py` | Added `JIRA = "jira"`. |
| `plugins_panel.py` | `_JiraDetail` inline (copy of `_VercelDetail`), `_jira_icon` (drawn double-chevron), catalog tuple → live, 4th stack page (index 3), `_open_detail` / `_sync_cards` branches. |
| `terminal_panel.py` | Builds `JiraController`, `jira=` kwarg, `_wire_jira_for`, `_on_jira_connected/_disconnected` nudges, teardown. |

### Data model / entitlements

Reuses `public.plugin_connections` with `provider='jira'`. **No migration.**
Reuses `entitlements.plugins_enabled(plan)` (Pro gate) unchanged.

### Tests

`test_jira_mcp.py`, `test_jira_controller.py` (copies of the Vercel ones);
`test_plugin_store.py` §7, `test_plugins_panel.py` §6 (with a `FakeJira` stub).

---

## 14. Multi-agent MCP targets — GitHub on every agent (2026-09-01)

Until now all three plugins only wrote **Claude Code**'s `~/.claude.json`. They now
write the right MCP config for **every coding agent AgentDeck can launch** that is
installed on the machine, in that agent's own format and location.

### Supported target agents (11)

`claude`, `codex`, `copilot`, `gemini`, `cursor-agent`, `opencode`, `amp`,
`antigravity`, `qwen`, `crush`, `goose`. **`aider` is excluded** — no native MCP
support (feature PRs closed unmerged as of 0.86); the UI shows a "not supported by
Aider" note in an Aider workspace.

### Per-agent config format

| key | file (Windows) | env override | fmt | server map | url field | `type` value | notes |
|---|---|---|---|---|---|---|---|
| claude | `~/.claude.json` | — | json | `mcpServers` | `url` | `http` | unchanged |
| codex | `~/.codex/config.toml` | `CODEX_HOME` | toml | `mcp_servers` | `url` | — | + root `experimental_use_rmcp_client = true`; bearer via `bearer_token` (no header → toolset scoping is dropped for Codex) |
| copilot | `~/.copilot/mcp-config.json` | `COPILOT_HOME` | json | `mcpServers` | `url` | `http` | `tools:["*"]`; **GitHub only** (remote-OAuth support unconfirmed) |
| gemini | `~/.gemini/settings.json` | — | json | `mcpServers` | `httpUrl` | — | |
| cursor-agent | `~/.cursor/mcp.json` | — | json | `mcpServers` | `url` | — | |
| opencode | `~/.config/opencode/opencode.json` | `XDG_CONFIG_HOME` | json | `mcp` | `url` | `remote` | `enabled:true`; auto-DCR OAuth (may open a browser on first use) |
| amp | `~/.config/amp/settings.json` | `AMP_SETTINGS_FILE` | json | `amp.mcpServers` (flat dotted key) | `url` | — | |
| antigravity | `~/.gemini/config/mcp_config.json` | — | json | `mcpServers` | `serverUrl` | — | different file from Gemini CLI — never cross-write |
| qwen | `~/.qwen/settings.json` | — | json | `mcpServers` | `httpUrl` | — | Gemini fork |
| crush | `%LOCALAPPDATA%\crush\crush.json` | — | json | `mcp` | `url` | `http` | |
| goose | `%APPDATA%\Block\goose\config\config.yaml` | — | yaml | `extensions` | `uri` | `streamable_http` | `enabled:true`, `bundled:false`, entry needs `name` == server name |

### New modules

| Module | What it does |
|---|---|
| `mcp_io.py` | Format-agnostic config IO — `load` / `dump` / `locked` / `get_in` / `as_item` for JSON (stdlib), TOML (read `tomllib`, write `tomlkit`), YAML (`ruamel.yaml`). Atomic `<name>.adk<pid>.tmp` + `os.replace`. A missing writer lib → that format's adapters no-op. `locked(path)` is a process-wide re-entrant lock that serialises same-file read-modify-writes (replaces the old `singleShot(0/250/400)` stagger). |
| `mcp_targets.py` | One `McpTarget` adapter per agent (`_TARGETS`), the capability model (`caps(key)` → `{"mcp","mcp_remote_headers","mcp_oauth","format"}`), `render_entry` (canonical spec → that agent's entry shape), `write_server` / `remove_server`, `oauth_hint`, and `McpLedger`. |

New deps: `tomlkit`, `ruamel.yaml` (both pure-Python-importable; guarded).
`config.py` gains `plugins_wire_all_agents` (default `True`).
`agents.py` gains `agent_key_for_command` and `installed_agent_keys`.

### Canonical spec

The `*_mcp.py` injectors build one transport-agnostic dict; `render_entry` turns it
into each agent's own shape:

```python
{"transport": "http"|"stdio",
 "url": str, "headers": {str:str}, "bearer": str|None,   # http
 "command": str, "args": [str], "env": {str:str},        # stdio (Claude only)
 "oauth": bool}                                          # True => tokenless
```

### Capability model — who gets what

* **GitHub** injects a bearer token, so it wires **every** agent with
  `mcp_remote_headers` (all 11 — Codex via `bearer_token`).
* **Vercel / Jira** are tokenless; the agent runs the MCP OAuth handshake itself.
  They wire only agents in `mcp_targets.OAUTH_ALLOWLIST` — **`{"claude"}` today**.
  Phase 3 widens this set one agent at a time as each in-pane OAuth command is
  verified. `oauth_hint(agent, server)` supplies the per-agent instruction
  (`/mcp` for Claude, `codex mcp login <server>` for Codex, `/mcp auth` for
  Gemini/Qwen, …) shown in the detail page and the status bar.

### The ledger — `%APPDATA%\multi-terminal\mcp_state.json`

`{provider: {agent_key: {"server", "wrote_root_extra"}}}`. The authoritative record
of what `disconnect` must undo — more reliable than the inline
`x-agentdeck-managed` (`x_agentdeck_managed` for TOML/YAML) marker if a strict
parser drops unknown keys. `wrote_root_extra` records whether **we** added Codex's
global `experimental_use_rmcp_client` (so disconnect only strips it if we added it
and no other managed server remains). Removal deletes an entry iff the inline
marker is set **or** the ledger records it. `backfill_claude` seeds the ledger for
users who connected before it existed.

### Wiring scope

`plugins_wire_all_agents` (default `True`): a connected plugin is written into
**every installed agent** at user scope — "I connected GitHub" then works in
whatever agent the user opens. Only ever touches agents found on PATH; never
creates a config dir for an absent agent. Set `False` to scope wiring to the
active workspace's agent only. `terminal_panel._add_workspace` wires once per
session (servers are user-scope); the controllers also (re)wire on `__init__` and
on `connected`.

### Integration points

| File | Change |
|---|---|
| `github_mcp.py` / `vercel_mcp.py` / `jira_mcp.py` | `canonical_server()` + adapter-driven `inject(…, agent_keys=…, config_paths=…)` / `remove(…)`; `supports_agent` redefined (key or command → capability); `mcp_server_config` kept as a Claude-rendered alias; `github_mcp.review_supported()` (`{claude, codex}`). |
| `*_controller.py` | `_agent_is_claude` → `_target_agent_keys(agent_command)` (active agent + installed set); `ensure_wired` iterates; stagger dropped. |
| `plugins_panel.py` | `PluginsPanel(agents_provider=…)`; `_VercelDetail` / `_JiraDetail` info box is per-agent (`_oauth_auth_html`); "Enabled for: …" / "tools in: …" sub-lines; docstrings de-Claude-d. |
| `terminal_panel.py` | `agents_provider` wired to `github._target_agent_keys`; `_start_github_review` uses the workspace's agent (falls back to Claude for non-`review_supported` agents); `_on_{vercel,jira}_connected` status text via `_oauth_hint`. |
| `config.py` | `plugins_wire_all_agents` default + schema. |
| `agents.py` | `agent_key_for_command`, `installed_agent_keys`. |
| `requirements.txt` / `constraints.txt` | `tomlkit`, `ruamel.yaml` (+ `ruamel.yaml.clib`). |

### Tests

`test_mcp_io.py`, `test_mcp_targets.py` (new); `test_github_mcp.py` §8 loops every
agent; `test_vercel_mcp.py` / `test_jira_mcp.py` / `test_*_controller.py` updated
for the OAuth allowlist; `test_agents.py`, `test_plugins_panel.py` extended. All
suites redirect config + ledger via `ADK_MCP_CONFIG_DIR` / `ADK_MCP_STATE`.

### Phasing

* **Phase 0–2 (this change)** — `mcp_io` + `mcp_targets` + adapter refactor;
  **GitHub on all 11 agents**; Vercel/Jira stay Claude-only via `OAUTH_ALLOWLIST`.
* **Phase 3** — verify each agent's in-pane OAuth command, add it to
  `OAUTH_ALLOWLIST` one at a time.
* **Phase 4** — optional Settings toggle for `plugins_wire_all_agents`.

### Known risks

* `ruamel.yaml` / `tomlkit` must be collected by PyInstaller — verify in the
  frozen build (guarded imports mean the worst case is YAML/TOML agents no-op).
* Codex's `experimental_use_rmcp_client` is a **global** user setting; the
  `wrote_root_extra` ledger flag keeps disconnect from stripping a flag the user
  set themselves.
* Windows path variance per agent — every env override has a test.
* Each agent's exact format is verified against docs as of 2026-09; re-check when
  an agent ships a breaking config change.
