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
button (`Connect` → `Manage`). Eight live cards — **GitHub**, **Vercel**,
**Jira**, **GitLab**, **Linear**, **Supabase**, **Google Drive** and
**LinkedIn** (see §12 / §13 / §15 / §16 / §17 / §18 / §19) — with the rest
rendered disabled as `Coming soon` (Bitbucket, Sentry, Netlify). Five of them
are *thin*: the agent owns the OAuth and AgentDeck never handles a credential.
The three exceptions are GitHub (a token vault), Google Drive — Google has no
dynamic client registration, so the user supplies an OAuth client (§18) — and
LinkedIn, which has no hosted MCP server at all, so AgentDeck runs one locally
(§19).

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
| **P5 — More providers** | **GitLab + Linear shipped** (§15 / §16, thin OAuth plugins, v0.15.0). **Supabase planned next** (§17, database review) — Bitbucket / Sentry after. | P4 |

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
* **Vercel / Jira / GitLab / Linear** are tokenless; the agent runs the MCP OAuth
  handshake itself. As of **2026-09-07 `mcp_targets.OAUTH_ALLOWLIST` is every
  MCP-capable agent** (all 11 — `aider` has no MCP support and isn't in
  `_TARGETS`). `oauth_hint(agent, server)` supplies the per-agent instruction
  (`/mcp` for Claude, `codex mcp login <server>` for Codex, `/mcp auth` for
  Gemini/Qwen, a first-use browser prompt for the rest) shown in the detail page
  and the status bar. Hold one agent back with `McpTarget.oauth = False`.
  *(Verified in-pane: Claude `/mcp`, opencode auto-DCR. The other nine are wired
  on the strength of their documented remote-MCP OAuth support; a dead entry on
  an agent that can't complete the flow is inert and is removed on disconnect.)*

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

* **Phase 0–2 (2026-09-01)** — `mcp_io` + `mcp_targets` + adapter refactor;
  **GitHub on all 11 agents**; Vercel/Jira stayed Claude-only via `OAUTH_ALLOWLIST`.
* **Phase 3** — verify each agent's in-pane OAuth command, add it to
  `OAUTH_ALLOWLIST` one at a time.
  * **opencode (2026-09-02)** — `OAUTH_ALLOWLIST = {"claude", "opencode"}`. Vercel
    + Jira now wire `~/.config/opencode/opencode.json` (`mcp` map, `type:"remote"`,
    `enabled:true`, tokenless); opencode runs the DCR/PKCE flow itself on first
    tool use. Detail pages gained a **Re-sync to agents** button
    (`_VercelDetail._on_resync` / `_JiraDetail._on_resync` → `controller.ensure_wired()`)
    so a plugin connected before a new agent was installed can be pushed to it
    without an app restart.
  * **all agents (2026-09-07, v0.15.0)** — `OAUTH_ALLOWLIST` is now every entry in
    `_TARGETS` (`copilot`'s `oauth=False` guard dropped). Every tokenless plugin
    (Vercel / Jira / GitLab / Linear) writes into every installed MCP-capable
    agent; each agent's authorise command comes from `oauth_hint()`. Only Claude
    and opencode are verified end-to-end; the rest rely on documented support and
    fail inert.
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

---

## 15. GitLab plugin (thin) — shipped v0.15.0 (2026-09-07)

The fourth live card. **A clone of the Vercel plugin (§12)** — the `_mcp` /
`_controller` modules are byte-identical bar the constants and copy.

### Why it's thin

GitLab's official MCP server is **hosted and OAuth-only** —
`https://gitlab.com/api/v4/mcp`, transport `type: "http"`, OAuth 2.0 with Dynamic
Client Registration. It does not take an API bearer token on this path, and there
is no official local binary to ship. The agent runs the OAuth itself (Claude via
`/mcp`, opencode via auto-DCR, the rest via `oauth_hint()`'s per-agent command).
So AgentDeck never handles a GitLab token — status is *"Enabled"*, not
*"Connected as @user"*.

**gitlab.com only in v1.** A self-hosted instance is `https://<host>/api/v4/mcp`;
supporting that needs a per-connection URL field — future. Override
`gitlab_mcp.REMOTE_MCP_URL` meanwhile. (Mirrors Jira's "Cloud-only".)

### The injected block (root `mcpServers.gitlab` of `~/.claude.json`)

```json
{ "type": "http", "url": "https://gitlab.com/api/v4/mcp", "x-agentdeck-managed": true }
```

The MCP server is named **`gitlab`** (matches the AgentDeck provider key,
`plugin_store.GITLAB`, and the `plugin_connections.provider` value). No `headers`,
no toolset filtering — GitLab's OAuth consent screen is where scope is chosen.

### Modules

| Module | Notes |
|---|---|
| `gitlab_mcp.py` | Copy of `vercel_mcp.py`; `_SERVER_NAME = "gitlab"`, `REMOTE_MCP_URL = "https://gitlab.com/api/v4/mcp"`, `plugin_store.GITLAB`. `inject`/`remove`/`_strip_managed` are transport-agnostic — they key only on the name + `x-agentdeck-managed`. |
| `gitlab_controller.py` | Copy of `vercel_controller.py` → `GitLabController`; `provider="gitlab"` in the Supabase mirror. No stagger (`mcp_io.locked()` serialises writes). |
| `plugin_store.py` | Added `GITLAB = "gitlab"`. |
| `plugins_panel.py` | `_GitLabDetail` inline (copy of `_JiraDetail`), `_gitlab_icon` (drawn triangle fan), catalog tuple → live, 5th stack page (index 4), `_open_detail` / `_sync_cards` branches, `PluginsPanel(gitlab=…)` kwarg. |
| `terminal_panel.py` | Builds `GitLabController`, `gitlab=` kwarg, `_wire_gitlab_for`, `_on_gitlab_connected/_disconnected` nudges, teardown. |

### Data model / entitlements

Reuses `public.plugin_connections` with `provider='gitlab'`. **No migration.**
Reuses `entitlements.plugins_enabled(plan)` (Pro gate) unchanged.

### Tests

`test_gitlab_mcp.py`, `test_gitlab_controller.py` (copies of the Jira ones);
`test_plugin_store.py` §8, `test_plugins_panel.py` §7 (with a `FakeGitLab` stub).

### Known: same "(re)start the agent then `/mcp`" caveat as Vercel / Jira

`claude` reads `.claude.json` at launch. After enabling GitLab the user restarts
the agent (`↻`) **and then runs `/mcp`** to authorise. `_on_gitlab_connected`
re-injects and shows a status-bar nudge saying so.

## 16. Linear plugin (thin) — shipped v0.15.0 (2026-09-07)

The fifth live card. **A near-exact clone of the GitLab plugin (§15) / Vercel
plugin (§12).**

### Why it's thin

Linear's official MCP server is **hosted and OAuth-only** —
`https://mcp.linear.app/mcp`, streamable HTTP (`type: "http"`), OAuth 2.1 with
Dynamic Client Registration. No API-token path in this flow, no local binary.
The agent authorises itself (Claude `/mcp`, opencode auto-DCR, others via
`oauth_hint()`). AgentDeck never handles a Linear token — status is *"Enabled"*.

The wired endpoint is **read-write**. Linear also serves read-only tools at
`https://mcp.linear.app/mcp/readonly` — a future per-connection toggle; switch
`linear_mcp.REMOTE_MCP_URL` meanwhile.

### The injected block (root `mcpServers.linear` of `~/.claude.json`)

```json
{ "type": "http", "url": "https://mcp.linear.app/mcp", "x-agentdeck-managed": true }
```

Server named **`linear`** (matches the provider key / `plugin_store.LINEAR` /
`plugin_connections.provider`).

### Modules

| Module | Notes |
|---|---|
| `linear_mcp.py` | Copy of `gitlab_mcp.py`; `_SERVER_NAME = "linear"`, `REMOTE_MCP_URL = "https://mcp.linear.app/mcp"`, `plugin_store.LINEAR`. |
| `linear_controller.py` | Copy of `gitlab_controller.py` → `LinearController`; `provider="linear"` in the Supabase mirror. |
| `plugin_store.py` | Added `LINEAR = "linear"`. |
| `plugins_panel.py` | `_LinearDetail` inline, `_linear_icon` (drawn stacked diagonal bars), catalog tuple → live, 6th stack page (index 5), `_open_detail` / `_sync_cards` branches, `PluginsPanel(linear=…)` kwarg. |
| `terminal_panel.py` | Builds `LinearController`, `linear=` kwarg, `_wire_linear_for`, `_on_linear_connected/_disconnected` nudges, teardown. |

### Data model / entitlements

Reuses `public.plugin_connections` with `provider='linear'`. **No migration.**
Reuses `entitlements.plugins_enabled(plan)` (Pro gate) unchanged.

### Tests

`test_linear_mcp.py`, `test_linear_controller.py`; `test_plugin_store.py` §9,
`test_plugins_panel.py` §8 (with a `FakeLinear` stub).

### Known: same "(re)start the agent then `/mcp`" caveat.

---

## 17. Supabase plugin (database review) — shipped v0.20.0 (2026-09-13)

The sixth card, and the first **"Database"** category. An agent in a pane
can inspect a user's *own* Supabase project — schema, tables, RLS policies,
migrations, logs, advisors — to answer "review this database" without the user
pasting SQL or screenshots. Scoped deliberately narrow (read-only) because a
database is a much sharper edge than a repo or a Kanban board.

> **⚠️ Naming disambiguation.** This plugin talks to the **user's own Supabase
> project** (whatever they run their app on). It has nothing to do with
> `supabase_auth.py`, `account.py` or the `plugin_connections` / `plugin_runs`
> tables described in §4 — those belong to **AgentDeck's own backend Supabase
> project**, which every AgentDeck install already talks to for accounts/sync.
> New modules are named `supabase_mcp.py` / `supabase_controller.py` (not
> `supabase_*_auth`) and both start with a docstring making this split explicit,
> the same way `jira_mcp.py` clarifies its provider key vs. the wire name
> `atlassian`.

### Why it's "thin-ish", not fully thin

Supabase's official **remote MCP server** is hosted and OAuth-only —
`https://mcp.supabase.com/mcp`, dynamic client registration, no manual PAT
needed. That part matches GitLab/Linear/Jira exactly: AgentDeck never handles a
Supabase token, the agent runs the OAuth itself (`/mcp` for Claude, per-agent
`oauth_hint()` for the rest), status is *"Enabled"*, not *"Connected as
@user"*.

The difference: Supabase's endpoint takes **URL query parameters** that meaningfully
change what the agent can do, so — unlike the pure tokenless clones — this
plugin needs a small settings form, not just a Connect button:

| Param | Meaning | AgentDeck default |
|---|---|---|
| `project_ref=<id>` | Scope to one project; disables org/account-level management tools | **required in v1** (no blank/org-wide option — see below) |
| `read_only=true` | All SQL runs under read-only Postgres permissions | **on, not exposed as a toggle in v1** |
| `features=<groups>` | Comma-separated tool groups: `database, docs, debugging, development, functions, branching, storage` | unset (Supabase's own default group set — everything but `storage`) |

Full URL AgentDeck builds:
`https://mcp.supabase.com/mcp?project_ref=<ref>&read_only=true`

**Why read-only is hard-locked in v1, not a checkbox:** the user asked for this
specifically as a *database review* tool, and Supabase's own docs flag prompt
injection via stored data as the primary risk of the write-capable tool surface. Shipping
read-only-only removes an entire class of "the agent ran a destructive query"
incidents for zero UX cost on a review workflow. A `read_only=false` toggle
(behind a confirmation, like GitHub's `write`/`admin` capabilities) is explicit
future work (§ Rollout, P2), not a day-one option.

**Why `project_ref` is required, not optional:** omitting it leaves every project
in the user's Supabase org reachable from one MCP connection — the opposite of
the narrow, per-workspace scoping every other plugin gets from a repo/board
picker. Connect is disabled until the field is filled; the detail page links out
to the Supabase dashboard to copy it (`Project Settings → General → Reference
ID`).

### The injected block (root `mcpServers.supabase` of `~/.claude.json`)

```json
{
  "type": "http",
  "url": "https://mcp.supabase.com/mcp?project_ref=abcdefghijklmnop&read_only=true",
  "x-agentdeck-managed": true
}
```

Server name **`supabase`** — matches the provider key, `plugin_store.SUPABASE`,
and (if mirrored) `plugin_connections.provider`.

### New modules

| File | Role | Notes |
|---|---|---|
| `supabase_mcp.py` | `canonical_server(settings)` builds the URL from `project_ref`/`read_only`/`features`; `inject()` / `remove()` — otherwise the same shape as `gitlab_mcp.py` (per-agent `agent_keys`, `mcp_targets.write_server`/`remove_server`, `McpLedger`). | Only injector in the family whose `canonical_server()` takes an argument — every other thin plugin's URL is a constant. |
| `supabase_controller.py` | `SupabaseController(QObject)` — `start_connect(project_ref, read_only=True, features=None)` validates `project_ref` (non-empty, plausible 20-ish char id) before writing the store row + injecting; `update_settings(...)` re-injects (idempotent overwrite, no disconnect/reconnect needed — confirmed `write_server` overwrites a managed entry in place); `disconnect()`; `ensure_wired()`. | Same `_Worker`/busy/signal shape as `gitlab_controller.py`. |
| `plugin_store.py` | Add `SUPABASE = "supabase"`. Add an optional `settings: Dict[str, str]` field to `PluginConnection` (default `{}`, included in `to_dict`/`from_dict` only when non-empty) — the first plugin that needs free-form per-connection config beyond the GitHub capability model. `CAPABILITIES`/`automation` stay unused noise for this provider, same as Vercel. | Backward compatible: existing rows with no `settings` key parse unchanged. |
| `plugins_panel.py` | `_SupabaseDetail` — project-ref text field + "Copy from dashboard" link + a static "Read-only" badge (not a checkbox in v1) + Connect/Disconnect + a **Re-sync to agents** button (reuse the GitLab/Linear one) for when `project_ref` changes; `_supabase_icon`; `_CATALOG` entry with new tag `"Database"`; new stack page + `_open_detail`/`_sync_cards` branches. | New filter tab **"Database"** appears automatically (tabs are derived from the tags present in `_CATALOG`). |
| `terminal_panel.py` | Builds `SupabaseController`, `supabase=` kwarg, `_wire_supabase_for`, `_on_supabase_connected/_disconnected` status nudges, teardown. | Same integration shape as every other plugin. |

### Data model / entitlements

Reuses `public.plugin_connections` with `provider='supabase'`. **No migration**
for the connection row itself. `project_ref` is **local-only** (`plugins.json`),
same trust boundary as GitHub's token — it does not roam to a second machine via
the cloud mirror in v1 (mirroring just the presence flag, like the other thin
plugins); a second machine reconnects and re-enters the ref. Revisit if users ask
for it to sync. Reuses `entitlements.plugins_enabled(plan)` (Pro gate) unchanged.

### Wiring / OAuth allowlist

Reuses `mcp_targets.OAUTH_ALLOWLIST` as-is (all 11 MCP-capable agents) —
Supabase's server needs the same DCR/PKCE dance every other hosted MCP here
does, nothing new to teach `mcp_targets.py`.

### Rollout

| Phase | Ships |
|---|---|
| **P0** | `supabase_mcp.py` + `supabase_controller.py` + `plugin_store.SUPABASE` + `settings` field, unit tests (`test_supabase_mcp.py`, `test_supabase_controller.py`, `test_plugin_store.py` §10) — headless, no UI yet. |
| **P1** | `_SupabaseDetail` (project-ref form, hard-locked read-only, Connect/Disconnect, Re-sync), catalog card live under "Database", `terminal_panel.py` wiring, `test_plugins_panel.py` §9 with a `FakeSupabase` stub. |
| **P2** | Optional `read_only=false` toggle behind an explicit confirmation dialog (mirrors GitHub's write/admin gating); `features=` checklist (Database/Docs/Debugging/Development/Functions/Branching/Storage) so a connection can be narrowed further; consider mirroring `project_ref` to the cloud row if users ask for cross-machine sync. |

### Known risks / open questions

* **Prompt injection via stored data** is Supabase's own documented top risk for
  this server (a comment or row value that looks like an instruction). Read-only
  mode limits the blast radius to "wrong answer", not "wrong write" — still worth
  a one-line warning in the detail page copy.
* **`project_ref` format isn't validated server-side by us** — a typo just makes
  the MCP connection fail per-tool-call in the pane; acceptable for v1, no need
  for AgentDeck to special-case Supabase API errors.
* **Same "(re)start the agent then `/mcp`" caveat** as every other thin plugin —
  `.claude.json` is read at launch, so a running pane needs a restart (↻) after
  connect, and again after any `update_settings()` change to `project_ref`.
* **Self-hosted / local Supabase** (`http://localhost:54321/mcp`, per Supabase's
  own docs) is out of scope for v1 — same "hosted only" call GitLab made for
  self-managed instances; revisit if asked.


## 18. Google Drive plugin (static OAuth client) — built 2026-09-17

The seventh live card, and **the first one that is not thin**. Read §12/§16
first for the thin-clone shape; this section is mostly about where Drive departs
from it and why.

### Why this one can't be thin

Every other hosted plugin is tokenless because its vendor implements **OAuth 2.1
Dynamic Client Registration** (RFC 7591): the agent registers itself at the
provider, so AgentDeck never handles a credential and "Connect" is one click.

Google does not implement DCR, and Google's token endpoint requires a client
secret *even for a Desktop-app client*. There is no way around it — it is a
Google constraint, not a choice of server. The community alternatives
(`workspace-mcp`, the archived `server-gdrive`) all demand the same thing.

So the user brings a **Desktop-app OAuth client** from their own Google Cloud
project. That is exactly what Google's own Drive-MCP guide instructs, it needs
no Google verification, and it keeps the blast radius on the user's own project.

> **A shipped AgentDeck-owned client is the obvious future upgrade** — one-click
> Connect, no console trip. It is not done yet because `drive.readonly` is a
> Google **restricted** scope: shipping it under our own client would require
> OAuth verification *and* a paid CASA security assessment. Until then, BYO.
> `gdrive_mcp.canonical_server` already reads the id from settings, so a built-in
> default client is a small change when that clears.

### The endpoint

Google's first-party server: `https://drivemcp.googleapis.com/mcp/v1`,
streamable HTTP. Eight tools — `search_files`, `read_file_content`,
`download_file_content`, `create_file`, `copy_file`, `get_file_metadata`,
`get_file_permissions`, `list_recent_files`.

Scopes: `drive.readonly drive.file` (read anything; create/edit what the client
touches), space-separated per RFC 6749 §3.3.

### Claude Code only — on purpose

`mcp_targets` gains a third capability, **`mcp_oauth_static`**, set only on the
`claude` target. The other ten agents shape static OAuth config differently
(Gemini/Antigravity use their own `oauth` schemas, opencode assumes DCR), so
writing Claude's shape into them produces an entry that *fails to authorise*
rather than one that fails loudly. Holding them back is the honest option; each
can be enabled individually once actually verified.

`render_entry` only emits a dict `oauth` for a target with `oauth_static`. A
**bool** `oauth` — what all six other plugins pass — is still metadata that is
never rendered, so their entries are byte-identical (regression-tested in
`test_gdrive_mcp.py` §3 and `test_mcp_targets.py` §8).

### The injected block (root `mcpServers.gdrive` of `~/.claude.json`)

```json
{
  "type": "http",
  "url": "https://drivemcp.googleapis.com/mcp/v1",
  "oauth": {
    "clientId": "1234-abc.apps.googleusercontent.com",
    "callbackPort": 8976,
    "scopes": "https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/drive.file"
  },
  "x-agentdeck-managed": true
}
```

`callbackPort` is fixed at **8976** because Google requires every redirect URI to
be pre-registered — it cannot be ephemeral. The user registers
`http://localhost:8976/callback` on their client.

### Where the secret lives — three places, one of them not ours

This is the part worth understanding before changing anything here.

1. **AgentDeck's vault** — `gdrive_secret.GDriveSecretStore`, a DPAPI/keyring
   blob at `%APPDATA%\multi-terminal\gdrive.bin` via the generic
   `secret_store.EncryptedJsonStore`. Same rules as the GitHub token: never
   cloud-mirrored, never in `plugins.json`.
2. **Claude Code's credential store** — `~/.claude/.credentials.json`, under
   `mcpOAuthClientConfig["gdrive|<hash of name+url>"].clientSecret`. Claude Code
   will **not** read a secret out of `.claude.json`; the only supported way in is
   `claude mcp add --client-secret`, which reads `MCP_CLIENT_SECRET` from the
   environment. That is why `gdrive_mcp.seed_secret()` shells out — the one
   plugin that runs a subprocess instead of only writing JSON. Note this copy is
   **plaintext JSON on Windows**; that is Claude Code's storage, not ours, and
   it is part of why the plugin insists on a *Desktop-app* client, whose secret
   RFC 8252 already treats as non-confidential.
3. **Never** in `plugins.json`, the Supabase mirror, or `argv` — the secret goes
   to the subprocess in the environment only, since argv is readable by other
   processes. Asserted in `test_gdrive_mcp.py` §8.

### Two ordering traps

**Connect order.** `claude mcp add` creates the server entry itself, so it would
sail past the "don't clobber a server the user wrote" guarantee that
`mcp_targets.write_server` gives every other plugin for free. Hence:
`foreign_server_agents()` → `seed_secret()` → `inject(seeded=True)`. The
`seeded` flag is what lets us restamp the entry `claude mcp add` just wrote with
our marker and scopes, and it is only ever passed after the conflict check came
back empty. `claude mcp add` also *refuses* an existing name while still exiting
0, so `seed_secret` removes first and checks what the command actually printed.

**Shutdown order.** `terminal_panel._shutdown_all()` calls `unwire_all()` on
every app exit. `unwire_all()` must therefore **not** drop Claude Code's secret —
relaunch rewrites the JSON entry but nothing re-seeds the credential, so the
plugin would silently stop working after the first restart. `forget_secret()`
belongs to `disconnect()` alone. The stored secret is keyed by server name + URL,
both of which we rewrite unchanged, so it survives the restart.
Regression-tested in `test_gdrive_controller.py` §8.

### Modules

| Module | Notes |
|---|---|
| `gdrive_mcp.py` | Closest to `supabase_mcp.py` (settings-aware). Adds `seed_secret()` / `forget_secret()` (subprocess) and `foreign_server_agents()` (pre-seed conflict check). Filters on `mcp_oauth_static`. |
| `gdrive_secret.py` | `GDriveSecretStore` over `secret_store.EncryptedJsonStore`; mirrors `github_auth.GitHubTokenStore`. |
| `gdrive_controller.py` | Copy of `supabase_controller.py` → `GDriveController`. Connect runs on a worker thread (subprocess) with a busy state; a failed seed rolls the vault back and emits the command to run by hand. |
| `mcp_targets.py` | New `McpTarget.oauth_static` + `caps()["mcp_oauth_static"]`; `render_entry` emits a dict `oauth`. |
| `plugin_store.py` | Added `GDRIVE = "gdrive"`. `settings` carries `client_id` / `scopes` — never the secret. |
| `plugins_panel.py` | `_GDriveDetail` (cloned from `_SupabaseDetail`), `_gdrive_icon`, catalog tuple, 8th stack page (index 7), `_open_detail` / `_sync_cards` branches, `PluginsPanel(gdrive=…)`. `_oauth_auth_html` gained a `need` arg so the authorise lines name only the agents actually wired. |
| `terminal_panel.py` | Builds `GDriveController`, `gdrive=` kwarg, `_wire_gdrive_for`, `_on_gdrive_connected/_disconnected` nudges, teardown. |
| `packaging/AgentDeck.spec` | `hiddenimports` += the three gdrive modules (and the two supabase ones, which were missing — statically reachable, so not a broken build, just a gap in the defensive list). |

### Data model / entitlements

Reuses `public.plugin_connections` with `provider='gdrive'`. **No migration** —
`provider` is unconstrained free text. The mirrored row is presence-only:
`external_login` empty, no client id, no secret. Reuses
`entitlements.plugins_enabled(plan)` (Pro gate) unchanged.

### The user's setup, once

Google Cloud console → enable the Drive API → OAuth consent screen (add yourself
as a test user) → Credentials → Create OAuth client ID → **Desktop app** → add
`http://localhost:8976/callback` as a redirect URI → paste the ID and secret into
the card. Then restart the agent in a pane and run `/mcp` to authorise.

### Tests

`test_gdrive_mcp.py` (78 checks), `test_gdrive_controller.py` (63);
`test_plugin_store.py` §11, `test_plugins_panel.py` §10 (`FakeGDrive` stub),
`test_mcp_targets.py` §8. The controller suite is fully offline — a stub shim
stands in for the `claude` binary and records its argv and environment.

### Verified against Claude Code 2.1.274

`claude mcp add --client-id/--client-secret/--callback-port` exists and works;
`claude mcp get gdrive` reports *"client_id configured, client_secret
configured"* and the server reaches **Connected**. The DCR regression reported in
claude-code#67258 / #38102 (a configured `clientId` ignored in favour of DCR, on
2.1.172) did **not** reproduce. If it resurfaces on a future version the card is
blocked upstream — nothing in AgentDeck can work around it; the fallback would be
the `workspace-mcp` stdio server, which `render_entry`'s stdio branch already
supports for Claude only, at the cost of a `uvx` runtime dependency.

### Known caveats

* Same "(re)start the agent, then `/mcp`" caveat as every other plugin.
* The end-to-end browser authorisation has not been exercised with a real Google
  client — that needs the user's own Cloud project.
* Only Claude Code is wired (above). The card says so, with the reason in the
  badge tooltip.

---

## 19. LinkedIn plugin (our own local MCP server) — built 2026-09-19

The ninth live card, and the one that departs furthest from every other plugin:
**AgentDeck ships the MCP server**. Read §18 first for the not-thin shape; this
section is about what LinkedIn forces on top of it.

What it is for: **an agent that hunts jobs on its own** — searches on a
schedule, dedupes against what it already showed you, scores against your CV,
drafts tailored applications and tracks the pipeline.

### Why this one is neither thin nor Drive-shaped

Drive at least had a first-party hosted MCP server. LinkedIn has neither half of
the thin pattern:

* **No first-party MCP server.** Everything on the market is third-party — Apify
  actors wrapping the public jobs endpoints, or community scrapers driving a
  logged-in session with the member's `li_at` cookie.
* **No self-serve job data.** LinkedIn's open permissions are exactly two
  products: *Sign In with LinkedIn* (OIDC — `openid profile email`) and *Share on
  LinkedIn* (`w_member_social`). Job search, Recruiter, Sales Navigator and
  everything else sit behind the partner program: company review, ~1–4 weeks,
  and in practice not granted to a desktop tool.
* **No DCR, and no PKCE for public clients either**, so — as with Drive — the
  user brings their own app and a client *secret* is unavoidable.

So `linkedin_server.py` is the server, launched over stdio. That is a cost (a
tool surface and a process to package) and two benefits: **no credential is ever
written into an agent's config**, and no OAuth capability is needed, so the
plugin reaches far more agents than Drive does.

### Three tiers, switched on separately

One card, three independent switches, because they carry very different risk.

| Tier | Source of truth | Tools | Risk |
|---|---|---|---|
| **1 — Official** | LinkedIn OAuth, BYO app | `linkedin_me`, `linkedin_post` | None; fully sanctioned |
| **2 — Job data** | BYO provider key (Apify actor / JSearch) | `search_jobs`, `job_details` | None to AgentDeck; user pays per call |
| **3 — Session** *(off by default)* | the member's own `li_at` | `saved_jobs`, `my_applications`, `unread_messages` | LinkedIn UA §8.2 — account restriction |

Tier 1 is implied by connecting. Tier 2 turns on when a provider key is stored.
**Tier 3 is opt-in behind a confirm dialog that names the risk in plain words**,
is rate-limited (`MIN_INTERVAL` 3 s, `MAX_PER_HOUR` 60) and is read-only — there
is no POST anywhere in `linkedin_session.py`, and a test asserts it.

The local pipeline tools (`shortlist`, `track_application`, `list_applications`,
`draft_application`) ride on tier 1: they touch no network at all.

### The line this plugin does not cross

**No tool submits an application.** `linkedin_draft_application` returns the
posting plus the user's CV and stops; the agent writes the letter, the human
sends it. Auto-apply is the fastest way to get an account restricted and it is
what makes the output worthless to whoever reads it. `test_linkedin_server.py`
§3 asserts no tool is named apply/submit and that the only LinkedIn write path
is the sanctioned post API. Changing that needs a design decision and a section
here, not a flag.

### The injected block (root `mcpServers.linkedin`)

```json
{
  "command": "C:\\Users\\<user>\\AppData\\Local\\AgentDeck\\current\\AgentDeck.exe",
  "args": ["--linkedin-mcp"],
  "env": {},
  "x-agentdeck-managed": true
}
```

No credential on the command line, none in `args`, none in `env` — the server
reads the vault itself, in-process. Compare §18's note about argv being readable
by other processes: here the problem cannot arise.

**The frozen-executable trap.** In a PyInstaller build `sys.executable` is
`AgentDeck.exe`, so the entry cannot be `python -m linkedin_server`. It
re-enters the app through an argv sentinel, and `main.py` checks it **before
every other import** — before Qt, before the Velopack bootstrap, before the
crash handler. `test_linkedin_mcp.py` §3 asserts that ordering against the file
itself, because a later check would mean every agent silently launching a second
copy of the whole GUI. From source the spec falls back to
`sys.executable -u linkedin_server.py`.

### Which agents get it — `mcp_targets` grew an stdio branch

`render_entry`'s stdio branch used to be "Claude only, keep it simple". It is now
shape-driven: `McpTarget` carries `stdio`, `stdio_command_field`,
`stdio_args_field`, `stdio_env_field` and `stdio_type_value`, and `caps()` gained
**`mcp_stdio`**.

Verified and wired: **Claude Code, Codex, Copilot CLI, Gemini, Qwen, Cursor
Agent, Amp, Antigravity** (the MCP-standard `command`/`args`/`env` shape; Copilot
additionally tags `"type": "local"`, Codex writes the TOML spelling).

Held back at `stdio=False`: **opencode** (its local servers take `command` as an
*array* plus `"type": "local"`), **Crush** (`"type": "stdio"`) and **Goose**
(`cmd`, not `command`). Their shapes are believed-but-not-verified, and §18's
doctrine applies: a wrong local-server shape produces a server that fails to
*start*, which is worse than one that fails loudly. Enabling each is a one-line
change plus a check against the real agent.

Claude's rendered stdio entry is byte-identical to before (regression-tested in
`test_mcp_targets.py` §5).

### Where the credentials live

`linkedin_secret.LinkedInSecretStore` — one DPAPI/keyring blob at
`%APPDATA%\multi-terminal\linkedin.bin`, holding `client_secret`,
`provider_key`, `li_at`, `access_token` and `token_expires`. Unlike Drive
(§18) **nothing is handed to an agent out-of-band**: there is no `claude mcp
add`, no second plaintext copy, no subprocess. `plugins.json` carries only
non-secrets (`client_id`, `tiers`, `provider`, `actor`, `resume_path`), and the
Supabase mirror carries presence plus which tiers are on — asserted in
`test_linkedin_controller.py` §2/§4/§5.

`disconnect()` clears the whole vault — token, secret, provider key and, above
all, the session cookie. It deliberately does **not** clear
`linkedin_jobs.json`: a job hunt's history outlives a reconnect, and deleting
someone's application record because they toggled a plugin would be the wrong
call.

### No refresh token, by LinkedIn's design

Refresh tokens are a partner-program feature. A self-serve app gets a ~60-day
access token and nothing to renew it with, so the controller exposes
`token_expired` and the card says "reconnect" rather than silently failing on
the next call.

### Two traps worth knowing

**stdout is not UTF-8 on Windows.** A pipe inherits the ANSI code page (cp1252
here), so the first tool description containing `→` killed the server mid-write
— found by the suite, not by a user. `main()` now reconfigures both streams to
UTF-8 *and* serialises every response with `ensure_ascii=True`, so a stream that
refuses to be reconfigured still cannot produce an unencodable byte. Newlines
are pinned too, since MCP's stdio framing is newline-delimited.

**`config_dir()` ignores `%APPDATA%`.** It resolves through platformdirs'
known-folder API, so redirecting the environment variable does *not* sandbox a
test — the first run of `test_linkedin_server.py` wrote a vault and a pipeline
into the developer's real config directory. `plugin_store`, `linkedin_secret`
and `linkedin_store` now honour `ADK_PLUGIN_STORE` / `ADK_LINKEDIN_VAULT` /
`ADK_LINKEDIN_JOBS` (the `ADK_MCP_STATE` convention), the suites set all three —
so the subprocess inherits them — and `test_linkedin_server.py` §10 asserts the
real directory was never touched.

### Automation = plugin + skill + routine

The server is only tools. The loop is assembled from features that already
exist, which is the argument for a plugin rather than a bespoke "Jobs" panel:

1. **The job-hunt skill** — `linkedin_controller.JOB_HUNT_SKILL`, installed from
   the card into `SkillsStore` (imported, not written, so installing twice gives
   a second copy rather than overwriting the criteria you filled in). Holds the
   roles, stack, bar for shortlisting, and the no-apply rule.
2. **The routine** — "Create the daily job-hunt routine" adds a `Routine` for
   09:00 on weekdays: *search → dedupe → score → shortlist → draft → digest*,
   then opens the Routines panel with it ready to edit.
3. **The status bar + Notes** deliver the result where the user already looks.

### Modules

| Module | Notes |
|---|---|
| `linkedin_server.py` | **New kind of module for this repo**: the MCP server. Newline-delimited JSON-RPC 2.0 over stdio, stdlib only (same call as `supabase_auth` making plain HTTPS calls rather than carrying an SDK). Re-reads settings per request, so a tier switched off mid-session takes effect on the next call. |
| `linkedin_mcp.py` | Injector, Qt-free. `canonical_server()` returns the frozen-aware **stdio** spec; filters on `caps()["mcp_stdio"]`. Otherwise the §16 shape. |
| `linkedin_secret.py` | The one vault (above). Merge semantics: `None` leaves a field alone, `""` forgets it — so switching a tier off never wipes another tier's credential. |
| `linkedin_auth.py` | OAuth authorization-code + loopback on **:8977** (fixed; LinkedIn pre-registers redirect URLs). No PKCE is available, so `state` is the whole CSRF defence and a *missing* state is rejected, unlike the Supabase flow. |
| `linkedin_api.py` | The sanctioned surface only: OIDC `userinfo` + `/rest/posts`. `REST_VERSION` is a maintenance item — a 426 means bump it. |
| `linkedin_jobs.py` | Tier-2 provider adapters (`apify`, `jsearch`) + a forgiving normaliser: providers disagree about field names far more than about content. Apify has no default actor on purpose — pointing someone's API budget at a guessed slug would be worse than asking. |
| `linkedin_session.py` | Tier 3. Read-only, throttled, endpoints in one dict, and **unverified against live LinkedIn** — treat a parse failure as "LinkedIn moved it". |
| `linkedin_store.py` | The pipeline: `mark_seen` returns only genuinely new postings (the dedupe the automation rests on), and statuses move forward only, with `closed` reachable from anywhere. |
| `linkedin_controller.py` | Qt bridge, cloned from `gdrive_controller`. Three switches rather than one connection; `SESSION_WARNING` and `JOB_HUNT_SKILL` live here. |
| `mcp_targets.py` | The stdio branch + `mcp_stdio` capability (above). |
| `plugin_store.py` | `LINKEDIN = "linkedin"`; `settings` carries `client_id` / `tiers` / `provider` / `actor` / `resume_path`, never a secret. Plus the `ADK_PLUGIN_STORE` test redirect. |
| `plugins_panel.py` | `_LinkedInDetail` (9th card, stack index 8), `_linkedin_icon`, `routine_requested` / `skill_requested` signals. The session confirm is `self._confirm`, replaceable by the suite — a modal `QMessageBox` hangs a headless test forever. |
| `terminal_panel.py` | Builds `LinkedInController`, `linkedin=` kwarg, `_wire_linkedin_for` at all five wiring sites, `_create_plugin_routine` / `_create_plugin_skill`, connect/disconnect status lines, teardown. |
| `main.py` | The `--linkedin-mcp` sentinel, above every import. |
| `packaging/AgentDeck.spec` | `hiddenimports` += the nine linkedin modules — none is statically reachable from the GUI import graph, so without this the frozen build ships a plugin that dies on first tool call. |

### Data model / entitlements

Reuses `public.plugin_connections` with `provider='linkedin'`. **No migration** —
`provider` is unconstrained free text (same as §15–§18). Reuses
`entitlements.plugins_enabled(plan)` (Pro gate) unchanged.

### The user's setup, once

LinkedIn developer portal → create an app (it needs a Company Page it
administers) → **Products** tab → request *Sign In with LinkedIn using OpenID
Connect* and *Share on LinkedIn*, both granted without review → **Auth** tab →
add `http://localhost:8977/callback` as a redirect URL → paste the client id and
secret into the card. Tier 2 additionally wants a provider API key (and, for
Apify, an actor id). Then restart the agent in a pane — there is nothing to
authorise, the server is local.

### Tests

`test_linkedin_store.py` (44), `test_linkedin_mcp.py` (46),
`test_linkedin_jobs.py` (39 — providers *and* session, stubbed transport),
`test_linkedin_server.py` (58, including a real subprocess over a real pipe),
`test_linkedin_controller.py` (57); plus `test_plugin_store.py` §12,
`test_plugins_panel.py` §11 (`FakeLinkedIn`) and `test_mcp_targets.py` §5.
All offline. 244 new checks; the full plugin-related suite is green, as is
`test_panel.py`.

### Known caveats

* **Tier 3 is unverified against live LinkedIn.** The voyager endpoints are
  undocumented and this was written without a live session to test against, so
  treat the first real run as the verification step; failures are reported as
  "LinkedIn changed its internal API" rather than as a crash.
* **Tier 1 and tier 2 have not been exercised against real credentials** — that
  needs the user's own LinkedIn app and provider key. Every failure path is
  covered offline, the happy path is not.
* opencode, Crush and Goose are held back (above).
* `REST_VERSION` (`202601`) will need bumping within about a year.
* Same "(re)start the agent" caveat as every other plugin.

---
