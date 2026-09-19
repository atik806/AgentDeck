"""Qt bridge for the LinkedIn plugin -- the surface ``plugins_panel`` talks to.

Closest relative is ``gdrive_controller``: there are real credentials, so
connecting validates first, runs off the GUI thread, and shows a busy state.
What's different is that this plugin is **three independent switches** rather
than one connection:

* ``official`` -- LinkedIn's own OAuth. Turned on by connecting at all.
  :meth:`start_connect` runs the loopback sign-in (``linkedin_auth``) on a
  worker thread and stores the access token in the vault.
* ``jobs`` -- a job-data provider key. :meth:`set_provider`.
* ``session`` -- the member's ``li_at`` cookie. :meth:`set_session_cookie`,
  and it is the caller's job to have shown the warning first: see
  :data:`SESSION_WARNING`, which the detail page puts in a confirm dialog.

Nothing here is ever mirrored to the account beyond presence and which tiers are
on -- no client id, no key, no cookie, no token (``_mirror_up``).

``linkedin_mcp`` / ``linkedin_secret`` / ``plugin_store`` do the real work and
stay Qt-free. See docs/PLUGINS.md 19.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal

import linkedin_mcp
from linkedin_secret import LinkedInSecretStore
from plugin_store import LINKEDIN, PluginConnection, PluginStore

__all__ = ["LinkedInController", "SESSION_WARNING", "TIERS", "JOB_HUNT_SKILL"]

#: The skill the card can install (``SkillsStore.import_markdown`` takes it as
#: is). It is a *template*: the agent is told to fill in the blanks by asking
#: once and editing this file, which is why the criteria are placeholders
#: rather than invented defaults. Pairs with the routine in ``plugins_panel``.
JOB_HUNT_SKILL = """---
name: job-hunt
description: How to run my LinkedIn job hunt -- my criteria, my CV, and the bar for shortlisting. Use whenever searching, scoring or drafting applications for jobs.
---

# Job hunt

## What I'm looking for

<!-- Fill these in. If they are still placeholders, ask me once, then edit
     this file so the next run doesn't have to ask again. -->

- **Roles**: e.g. senior backend / platform engineer
- **Stack**: e.g. Python, Postgres, async
- **Location**: e.g. remote (EU) or Berlin
- **Seniority**: e.g. senior / staff
- **Minimum salary**: e.g. 80k EUR
- **Deal-breakers**: e.g. no on-call, no crypto, no relocation

## The bar

Score every posting 0-100 on how well it matches the list above. Shortlist at
**70 or better**, and give a one-line reason -- the reason is what I read, not
the number. Say plainly when a run finds nothing worth shortlisting; a short
honest digest beats a padded one.

## Drafting

Write applications from my CV and the posting. Never claim experience my CV
does not show. Keep it under 250 words, concrete, and specific to the company.

**You cannot submit applications and must not try.** Draft, save the draft,
mark the job as `drafted`, and leave the sending to me.

## Tools

The LinkedIn plugin's tools do the work: search records everything in a local
pipeline and reports only what is new, so trust its "new" list rather than
re-reading old results.
"""

#: The three switches, in card order. ``official`` is implicit in connecting.
TIERS = ("official", "jobs", "session")

#: Shown before the session tier can be switched on. Deliberately blunt: this
#: is the one part of AgentDeck that can cost a user their LinkedIn account.
SESSION_WARNING = (
    "Session reading uses your own LinkedIn login cookie to read your saved "
    "jobs, applications and unread messages.\n\n"
    "LinkedIn's User Agreement prohibits automated access, and accounts have "
    "been restricted for it. AgentDeck keeps this read-only and slow, but the "
    "risk is real and it is yours.\n\n"
    "Turn it on anyway?"
)


class _Worker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], object], parent: Optional[QObject] = None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc) or exc.__class__.__name__)
            return
        self.done.emit(result)


class LinkedInController(QObject):
    """The LinkedIn-plugin surface. Safe to construct unconditionally."""

    connected = Signal(dict)      # {} -- dict kept so _on_* lambdas match GitHub
    disconnected = Signal()
    busy_changed = Signal(bool)
    error = Signal(str)
    #: A tier was switched on or off; the detail page re-reads state.
    tiers_changed = Signal()
    #: The member's display name arrived. Its own signal rather than a second
    #: ``connected``: that one means "a connection just happened", and firing it
    #: twice per sign-in showed the status-bar toast twice and re-ran the MCP
    #: wiring for nothing.
    profile_updated = Signal()

    def __init__(self, account=None, config: Optional[dict] = None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self._account = account
        self._config = config if config is not None else {}
        self._store = PluginStore()
        self._vault = LinkedInSecretStore()
        self._busy = False
        self._workers: set = set()

        # Already connected from a previous run? Re-write the entry now: it
        # embeds this install's executable path, so an update that moved the
        # exe leaves a stale command behind until we rewrite it.
        if self.is_connected:
            QTimer.singleShot(0, self.ensure_wired)

    # -- state -----------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._store.is_connected(LINKEDIN)

    @property
    def connection(self) -> Optional[PluginConnection]:
        return self._store.get(LINKEDIN)

    @property
    def login(self) -> str:
        conn = self.connection
        return conn.login if conn else ""

    @property
    def is_busy(self) -> bool:
        return self._busy

    @property
    def client_id(self) -> str:
        conn = self.connection
        return conn.settings.get("client_id", "") if conn else ""

    @property
    def provider(self) -> str:
        conn = self.connection
        return conn.settings.get("provider", "apify") if conn else "apify"

    @property
    def actor(self) -> str:
        conn = self.connection
        return conn.settings.get("actor", "") if conn else ""

    @property
    def resume_path(self) -> str:
        conn = self.connection
        return conn.settings.get("resume_path", "") if conn else ""

    @property
    def tiers(self) -> List[str]:
        conn = self.connection
        if conn is None:
            return []
        raw = str(conn.settings.get("tiers") or "official")
        return [t for t in TIERS if t in {x.strip() for x in raw.split(",")}]

    def tier_on(self, tier: str) -> bool:
        return tier in self.tiers

    @property
    def has_secret(self) -> bool:
        return self._vault.has("client_secret")

    @property
    def has_provider_key(self) -> bool:
        return self._vault.has("provider_key")

    @property
    def has_session_cookie(self) -> bool:
        return self._vault.has("li_at") and self._vault.has("li_jsession")

    @property
    def token_expires_at(self) -> float:
        try:
            return float(self._vault.get("token_expires") or 0)
        except (TypeError, ValueError):
            return 0.0

    @property
    def token_expired(self) -> bool:
        """True once LinkedIn's ~60-day token has lapsed. There is no refresh
        token on a self-serve app, so the only cure is reconnecting."""
        expires = self.token_expires_at
        return bool(expires and expires < time.time())

    # -- connect / disconnect --------------------------------------------

    #: What a brand-new connection starts with. Only ever applied to keys the
    #: stored connection doesn't already have -- see :meth:`start_connect`.
    _DEFAULT_SETTINGS = {"tiers": "official", "provider": "apify",
                         "actor": "", "resume_path": ""}

    def start_connect(self, client_id: str, client_secret: str) -> bool:
        """Run the LinkedIn OAuth sign-in and connect. Returns False (and emits
        ``error``) without connecting when a field is missing.

        This is also the *reconnect* path -- LinkedIn's self-serve tokens last
        ~60 days and cannot be refreshed, so every user comes back through here
        eventually. It therefore has to be non-destructive: see ``_done``.
        """
        if self._busy:
            return False
        cid = (client_id or "").strip()
        typed = (client_secret or "").strip()
        secret = typed or (self._vault.get("client_secret") or "")
        if not cid or not secret:
            self.error.emit(
                "Paste both the Client ID and the Client Secret from your "
                "LinkedIn app before connecting."
            )
            return False

        def _do():
            import linkedin_auth

            return linkedin_auth.sign_in(cid, secret)

        def _done(result):
            token = str((result or {}).get("access_token") or "")
            if not token:
                self.error.emit("LinkedIn didn't return an access token.")
                return
            expires = float((result or {}).get("expires_at") or 0)
            # The secret is only worth keeping now that LinkedIn has accepted
            # it: storing it up front left a rejected secret in the vault with
            # no way to clear it (Disconnect is hidden while disconnected).
            if not self._vault.save(client_secret=secret, access_token=token,
                                    token_expires=str(expires or "")):
                self.error.emit("Couldn't store the LinkedIn token securely.")
                return
            # Reconnecting must not undo the user's setup. Building a fresh
            # PluginConnection here reset `tiers` to official-only, `provider`
            # to apify and wiped the CV path -- silently switching off job
            # search and session reading while their credentials sat in the
            # vault, every time a token lapsed.
            conn = self.connection
            if conn is None:
                conn = PluginConnection(LINKEDIN, settings={})
            for key, value in self._DEFAULT_SETTINGS.items():
                conn.settings.setdefault(key, value)
            conn.settings["client_id"] = cid
            self._store.put(conn)
            self.ensure_wired()
            self._refresh_login()
            self._mirror_up(conn)
            self.connected.emit({})

        def _fail(message: str):
            self.error.emit(message)

        self._run(_do, _done, on_fail=_fail, busy=True)
        return True

    def disconnect(self) -> None:
        """Full disconnect: unwire every agent and clear *every* credential --
        the token, the client secret, the provider key and, above all, the
        session cookie. The pipeline store is left alone: a job hunt outlives a
        reconnect, and silently deleting someone's application history because
        they toggled a plugin would be the wrong call."""
        if self._busy:
            return
        self.unwire_all()
        self._vault.clear()
        self._store.remove(LINKEDIN)
        self._mirror_delete()
        self.disconnected.emit()

    # -- tiers ------------------------------------------------------------

    def _set_tiers(self, tiers) -> None:
        conn = self.connection
        if conn is None:
            return
        wanted = [t for t in TIERS if t in set(tiers)]
        if "official" not in wanted:
            wanted.insert(0, "official")   # implied by being connected at all
        conn.settings["tiers"] = ",".join(wanted)
        self._store.put(conn)
        self._mirror_up(conn)
        self.tiers_changed.emit()

    def set_provider(self, provider: str, api_key: str = "", actor: str = "") -> bool:
        """Configure (or, with an empty key, switch off) the job-data tier."""
        conn = self.connection
        if conn is None:
            return False
        name = (provider or "apify").strip().lower()
        key = (api_key or "").strip()
        # The credential first: writing the provider and actor to plugins.json
        # and *then* failing to store the key left the card claiming a provider
        # the key never reached.
        if key and not self._vault.save(provider_key=key):
            self.error.emit("Couldn't store the provider key securely on this machine.")
            return False
        conn.settings["provider"] = name
        conn.settings["actor"] = (actor or "").strip()
        self._store.put(conn)

        tiers = set(self.tiers)
        if key or self.has_provider_key:
            tiers.add("jobs")
        else:
            tiers.discard("jobs")
        self._set_tiers(tiers)
        return True

    def clear_provider_key(self) -> None:
        self._vault.save(provider_key="")
        tiers = set(self.tiers)
        tiers.discard("jobs")
        self._set_tiers(tiers)

    def set_session_cookie(self, li_at: str, jsession: str = "") -> bool:
        """Switch the session tier on with the member's own session cookies.

        Both halves are needed: LinkedIn's internal API validates the
        ``Csrf-Token`` header against the real ``JSESSIONID`` cookie of the same
        session, so ``li_at`` alone is refused.

        The caller must have shown :data:`SESSION_WARNING` and got a yes --
        this method does not ask, because the confirm belongs in the UI layer
        where a dialog can be tested and themed.
        """
        conn = self.connection
        if conn is None:
            return False
        cookie = (li_at or "").strip().strip('"')
        csrf = (jsession or "").strip().strip('"')
        if not cookie:
            self.error.emit("Paste the li_at cookie value, or leave the tier off.")
            return False
        if not csrf:
            self.error.emit(
                "Paste the JSESSIONID cookie too — LinkedIn checks it against "
                "li_at and rejects the read without it. Both are in your "
                "browser's cookies for linkedin.com."
            )
            return False
        if not self._vault.save(li_at=cookie, li_jsession=csrf):
            self.error.emit("Couldn't store the session cookie securely on this machine.")
            return False
        tiers = set(self.tiers)
        tiers.add("session")
        self._set_tiers(tiers)
        return True

    def clear_session_cookie(self) -> None:
        """Switch the session tier off and forget both cookies."""
        self._vault.save(li_at="", li_jsession="")
        tiers = set(self.tiers)
        tiers.discard("session")
        self._set_tiers(tiers)

    def set_resume_path(self, path: str) -> bool:
        """Point the drafting tool at a CV. Returns False (and emits ``error``)
        for a file the agent could only read as mojibake -- a .pdf or .docx is
        a binary, and the old behaviour was to accept it and hand the agent
        pages of replacement characters."""
        conn = self.connection
        if conn is None:
            return False
        text = (path or "").strip().strip('"')
        if text:
            import linkedin_server

            target = Path(text)
            if target.suffix.lower() not in linkedin_server.RESUME_SUFFIXES:
                self.error.emit(
                    f"A {target.suffix} CV can't be read as text. Export it to "
                    "Markdown or plain text and pick that file instead."
                )
                return False
            if not target.exists():
                self.error.emit(f"There's no file at {text}.")
                return False
        conn.settings["resume_path"] = text
        self._store.put(conn)
        self.tiers_changed.emit()
        return True

    # -- identity ---------------------------------------------------------

    def _refresh_login(self) -> None:
        """Best-effort: put the member's name on the card. A failure here must
        not fail the connect -- the plugin works without a display name."""
        token = self._vault.get("access_token")
        if not token:
            return

        def _do():
            import linkedin_api

            return linkedin_api.me(token)

        def _done(profile):
            conn = self.connection
            if conn is None:
                return
            conn.login = str((profile or {}).get("name") or "")
            self._store.put(conn)
            self.profile_updated.emit()

        self._run(_do, _done, on_fail=lambda _m: None)

    # -- MCP wiring ------------------------------------------------------

    def _target_agent_keys(self, agent_command: Optional[str] = None) -> list:
        """Which agents to write the LinkedIn MCP server into -- the workspace's
        agent plus, unless ``plugins_wire_all_agents`` is off, every installed
        agent. ``linkedin_mcp.inject`` further filters to the stdio-capable set."""
        import agents

        keys: set = set()
        k = agents.agent_key_for_command(agent_command) if agent_command else ""
        if not k:
            k = str(self._config.get("agent", "")).strip().lower()
        if k and k not in ("none", "custom"):
            keys.add(k)
        if self._config.get("plugins_wire_all_agents", True):
            keys |= set(agents.installed_agent_keys())
        return sorted(keys)

    def ensure_wired(self, folder: Optional[str] = None,
                     agent_command: Optional[str] = None) -> bool:
        """Add the LinkedIn MCP server to every stdio-capable target agent's
        user-scope config. Best-effort; returns True if any config changed."""
        if not self.is_connected:
            return False
        keys = self._target_agent_keys(agent_command)
        if not keys:
            return False
        try:
            return linkedin_mcp.inject(agent_keys=keys)
        except Exception:  # noqa: BLE001
            return False

    #: Back-compat alias -- older name for the same thing.
    def wire_if_connected(self) -> bool:
        return self.ensure_wired()

    def unwire_all(self) -> None:
        """Drop our MCP entry from every agent config. Called on app shutdown as
        well as disconnect, so it must not touch the vault -- the server reads
        its own credentials and the entry is rewritten on the next launch."""
        try:
            linkedin_mcp.remove()
        except Exception:  # noqa: BLE001
            pass

    # -- Supabase mirror (presence + tiers only, best-effort) -------------

    def _mirror_up(self, conn: PluginConnection) -> None:
        acc = self._account
        if acc is None or not getattr(acc, "is_signed_in", False):
            return
        session = getattr(acc, "session", None)
        if session is None:
            return
        row = {
            "provider": LINKEDIN,
            "external_login": "",
            # Which switches are on -- never the client id, key, cookie or token.
            "capabilities": list(self.tiers),
            "automation": {},
        }
        uid = getattr(session, "user_id", "")
        if uid:
            row["user_id"] = uid

        def _do():
            import supabase_auth

            return supabase_auth.rest_upsert("plugin_connections", row, session.access_token)

        self._run(_do, lambda _r: None, on_fail=lambda _m: None)

    def _mirror_delete(self) -> None:
        acc = self._account
        if acc is None or not getattr(acc, "is_signed_in", False):
            return
        session = getattr(acc, "session", None)
        if session is None:
            return

        def _do():
            import requests
            import supabase_auth

            requests.delete(
                f"{supabase_auth.SUPABASE_URL}/rest/v1/plugin_connections",
                headers={
                    "apikey": supabase_auth.SUPABASE_KEY,
                    "Authorization": f"Bearer {session.access_token}",
                },
                params={"provider": f"eq.{LINKEDIN}"},
                timeout=15,
            )
            return None

        self._run(_do, lambda _r: None, on_fail=lambda _m: None)

    # -- worker plumbing -----------------------------------------------

    def _set_busy(self, value: bool) -> None:
        if value != self._busy:
            self._busy = value
            self.busy_changed.emit(value)

    def _run(self, fn, on_done, *, on_fail=None, busy: bool = False) -> _Worker:
        worker = _Worker(fn, parent=self)

        def _cleanup():
            worker.deleteLater()
            self._workers.discard(worker)
            if busy:
                self._set_busy(False)

        def _handle_done(result):
            try:
                on_done(result)
            except Exception as exc:  # noqa: BLE001
                self.error.emit(str(exc))

        def _handle_fail(message: str):
            if on_fail is not None:
                on_fail(message)
            else:
                self.error.emit(message)

        worker.done.connect(_handle_done)
        worker.failed.connect(_handle_fail)
        worker.finished.connect(_cleanup)
        self._workers.add(worker)
        if busy:
            self._set_busy(True)
        worker.start()
        return worker

    def shutdown(self) -> None:
        for worker in list(self._workers):
            try:
                worker.requestInterruption()
                if not worker.wait(2000):
                    worker.terminate()
                    worker.wait(500)
            except Exception:  # noqa: BLE001
                pass
        self._workers.clear()
