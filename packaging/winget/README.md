# winget submission

Publishing AgentDeck to the winget community repo means users can
`winget install AtikShahriar.AgentDeck` — winget downloads and runs the
installer itself, so the SmartScreen "Unknown publisher" dialog does not appear
for that path (it can still surface on a plain browser download until the app is
code-signed / has reputation).

`manifests/a/AtikShahriar/AgentDeck/<version>/` holds the three manifest files in
the exact layout the `microsoft/winget-pkgs` repo expects.

## First submission (v0.3.1)

Submitted 2026-08-30 as a PR to `microsoft/winget-pkgs` (branch
`atik806:agentdeck-0.3.1`, files created via the GitHub API — the winget-pkgs
tree is too large to clone). Steps to reproduce / redo:

1. **Validate locally** (needs winget ≥ 1.6):
   ```
   winget validate --manifest packaging\winget\manifests\a\AtikShahriar\AgentDeck\0.3.1
   winget install --manifest packaging\winget\manifests\a\AtikShahriar\AgentDeck\0.3.1
   winget uninstall AgentDeck
   ```
   The install/uninstall round-trip is what the winget-pkgs CI runs in a
   sandbox; if uninstall isn't detected the PR just gets manual review.

2. **Fork + PR:**
   - Fork <https://github.com/microsoft/winget-pkgs>.
   - Add `manifests/a/AtikShahriar/AgentDeck/0.3.1/` on a branch in the fork.
   - Open a PR titled `New package: AtikShahriar.AgentDeck version 0.3.1`.
   - The `wingetbot` runs validation; unsigned installer → expect manual
     moderator review. A moderator merges.

   Or use **wingetcreate** to do the fork/PR for you:
   ```
   wingetcreate submit packaging\winget\manifests\a\AtikShahriar\AgentDeck\0.3.1
   ```

## Later versions — automated

`.github/workflows/release.yml` has an **Update winget manifest** step that runs
after the GitHub release is published: it downloads `wingetcreate`, regenerates
the manifest from the new `AgentDeck-win-Setup.exe` URL (hash recomputed
automatically), and opens the winget-pkgs PR.

It only runs if the **`WINGET_TOKEN`** repo secret is set:

- A GitHub PAT on a **fork of `microsoft/winget-pkgs`** under your account
  (`github.com/atik806/winget-pkgs`).
- Classic PAT: `public_repo` scope. Fine-grained: that fork + **Contents** and
  **Pull requests** read/write.
- No secret → the step logs "skipping" and the release still succeeds.

`wingetcreate update` publishes *updates only*, so the **first** submission must
be the manual PR above. Once 0.3.1 is merged into winget-pkgs, add the secret and
every future `v*` tag updates winget on its own.

To do a version manually anyway:
`wingetcreate update AtikShahriar.AgentDeck --version <x.y.z> --urls <setup-exe-url> --submit`

## Manifest notes

- `InstallerType: exe` + `Silent: --silent` — Velopack's `Setup.exe` silent flag.
- `Scope: user` / `ElevationRequirement: elevationProhibited` — Velopack installs
  per-user to `%LOCALAPPDATA%\AgentDeck`, no UAC.
- `UpgradeBehavior: install` — the app also self-updates via Velopack; running
  the newer setup over an install is fine.
- `InstallerSha256` is for the **published** release asset
  `AgentDeck-win-Setup.exe` (v0.3.1: `7EC40B553A903DAC13811FE4A2442912CAED580857C7CA817F29A37613C1F3DE`,
  from the release's `SHA256SUMS.txt`). Recompute if the asset is re-uploaded.
