# Isolated worktrees

*Feature landed 2026-09-07 on `feat/isolated-worktrees`. Pro-gated.*

## What it does

When you open a workspace with **"Isolate each terminal in its own git
worktree"** ticked, every pane gets its own `git worktree` checked out on a
throwaway branch. Several agents can then work the same repository in parallel
without touching each other's files or your real checkout. The **Worktrees**
panel (sidebar, below Routines) is where you review each one's diff and decide
what to do with it: **Merge to base**, **Open PR**, **Open in a pane**, or
**Discard**.

## Where things live

| Thing | Path |
|---|---|
| Store (which pane ↔ which worktree) | `%APPDATA%\multi-terminal\worktrees.json` |
| The scratch worktree trees | `%LOCALAPPDATA%\multi-terminal\worktrees\<repo-key>\<branch>\` |
| Scratch branch names | `agentdeck/<workspace-slug>/p<N>-<yymmdd-HHMM>-<hex>` |

`repo-key` is `sha1(normcase(abspath(repo root)))[:12]`. The store is
machine-local — it is **not** cloud-synced (the paths only mean something on
this disk).

## Modules

- **`git_worktree.py`** — the only place that shells out to `git`. Qt-free.
  `detect_repo` / `list_worktrees` / `add_worktree` / `remove_worktree` /
  `delete_branch` / `status` / `diff_stat` / `diff_text` / `is_dirty`
  (v1); `commit_all` / `merge_to_base` / `rebase_onto_base` / `push_branch` /
  `base_moved` (v2). Every subprocess passes `creationflags=CREATE_NO_WINDOW`
  (no console flash) and `GIT_TERMINAL_PROMPT=0` (a missing credential helper
  fails fast rather than hanging).
- **`worktree_store.py`** — `WorktreeRecord` + `WorktreeStore`, same shape as
  `routines_store`. `reconcile(live_by_repo)` is the crash-recovery entry
  point.
- **`worktree_panel.py`** — `WorktreePanel`, a pure view: reads the store and
  the read-only `git_worktree` helpers, emits `merge_requested` /
  `open_pr_requested` / `discard_requested` / `open_in_pane_requested` for
  `TerminalPanel` to carry out.

## How the merge stays safe

`git` refuses to check out `<base>` in a second worktree while your main
checkout is on it, so `merge_to_base`:

1. asks the caller to resolve a dirty worktree first (commit-all or cancel);
2. `git worktree add --detach <tmp> <base>` — an *ephemeral* worktree;
3. `git merge` inside `<tmp>`;
4. on conflict: collect the conflicted paths, `git merge --abort`, remove
   `<tmp>`, raise `WorktreeConflict` — **nothing changed**;
5. on success: `git update-ref refs/heads/<base> <new> <old>` — a
   compare-and-swap, atomic, and it never runs `checkout` in your tree. Your
   checkout simply shows "behind" afterwards.

## Crash recovery

`TerminalPanel._reconcile_worktrees_on_startup` runs once at launch:

- a record whose directory is gone → `orphaned`;
- a record git no longer registers → `orphaned`;
- an on-disk `agentdeck/*` worktree with no record → adopted as `(imported)`;
- a `pending_delete` record (its dir was locked when a pane closed) → retried,
  then dropped.

## Pane identity

`TerminalPane.pane_id` is a stable `p_<hex>` assigned at construction. Relayout
*reparents* panes rather than rebuilding them, and their list index shifts as
panes are added / closed / reordered, so the store keys the pane↔worktree
mapping off `pane_id`, never the index.

## Entitlement

`entitlements.worktrees_enabled(plan)` — Pro only, same tier as handoff /
routines. The nav item and the dialog checkbox stay visible for Free
(discoverability); the actions show the upgrade prompt. A workspace opened
isolated while Pro keeps its worktrees if the plan later lapses — Merge / Open
PR just stop being offered, and cleanup still runs.

## Not done yet (v2 candidates)

- Conversation handoff / routines don't create or reuse worktrees.
- No "base moved — rebase first" one-click in the panel (the badge shows, the
  merge still works via a real merge commit).
- Diff rendering for a very large repo is byte-capped, not moved onto a worker
  thread.
