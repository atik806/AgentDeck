"""Cross-device sync for the skill library -- the Pro half of Skills.

The local :class:`skills_store.SkillsStore` is always the working copy. This
controller mirrors it to ``public.skills`` (RLS: ``user_id = auth.uid()``) so
the same library follows the user to their other machines:

* **pull** once on launch / when the plan resolves to Pro -- merge cloud rows
  into the local store by ``slug`` (last-write-wins on ``updated_at``),
* **push** (debounced) whenever a skill is created / edited / deleted / rewritten
  by an agent.

Best-effort throughout: no account, Free plan, ``account_cloud_sync`` off, or a
failed request just means "local only" -- exactly how ``account.push_cloud_settings``
and the plugin ``_mirror_up`` behave. Deletes are soft (``deleted = true``) so a
delete on one machine propagates instead of the row reappearing on the next pull.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal

import entitlements

__all__ = ["SkillsCloud"]

_PUSH_DEBOUNCE_MS = 1500


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


class SkillsCloud(QObject):
    """Mirror ``store`` to ``public.skills`` for the signed-in account."""

    #: A pull merged remote changes into the local store -- the panel should
    #: reload and the agents should be re-materialized.
    pulled = Signal()

    def __init__(self, account, config: dict, store, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._account = account
        self._config = config if config is not None else {}
        self._store = store
        self._workers: set = set()
        self._known_slugs: set[str] = set()

        self._push_timer = QTimer(self)
        self._push_timer.setSingleShot(True)
        self._push_timer.setInterval(_PUSH_DEBOUNCE_MS)
        self._push_timer.timeout.connect(self._push_now)

    # -- gating -------------------------------------------------------

    def _session(self):
        acc = self._account
        if acc is None or not getattr(acc, "is_signed_in", False):
            return None
        if not self._config.get("account_cloud_sync", True):
            return None
        if not entitlements.cloud_sync_enabled(getattr(acc, "plan", "free")):
            return None
        return getattr(acc, "session", None)

    # -- pull -------------------------------------------------------

    def pull_soon(self) -> None:
        QTimer.singleShot(0, self.pull)

    def pull(self) -> None:
        session = self._session()
        if session is None:
            return

        def _do():
            import supabase_auth

            return supabase_auth.rest_select(
                "skills", session.access_token,
                params={"select": "slug,name,description,body,enabled,source,deleted,updated_at"},
            )

        self._run(_do, self._on_pulled, on_fail=lambda _m: None)

    def _on_pulled(self, rows) -> None:
        if not isinstance(rows, list):
            return
        changed = self._store.merge_cloud(rows)
        self._known_slugs = {s.slug for s in self._store.all()}
        if changed:
            self.pulled.emit()

    # -- push -------------------------------------------------------

    def push_soon(self) -> None:
        """Schedule a debounced push (coalesces a burst of edits)."""
        if self._session() is None:
            return
        self._push_timer.start()

    def _push_now(self) -> None:
        session = self._session()
        if session is None:
            return
        rows = self._store.cloud_rows()
        live = {r["slug"] for r in rows}
        # Slugs we pushed before that are gone now -> soft-delete them.
        tombstones = [
            {"slug": slug, "deleted": True, "updated_at": _now_iso()}
            for slug in (self._known_slugs - live)
        ]
        payload = rows + tombstones
        if not payload:
            self._known_slugs = live
            return
        uid = getattr(session, "user_id", "")
        for row in payload:
            if uid:
                row["user_id"] = uid

        def _do():
            import supabase_auth

            return supabase_auth.rest_upsert("skills", payload, session.access_token)

        self._run(_do, lambda _r: None, on_fail=lambda _m: None)
        self._known_slugs = live

    # -- worker plumbing -------------------------------------------

    def _run(self, fn, on_done, *, on_fail=None) -> _Worker:
        worker = _Worker(fn, parent=self)

        def _cleanup():
            worker.deleteLater()
            self._workers.discard(worker)

        def _handle_done(result):
            try:
                on_done(result)
            except Exception:  # noqa: BLE001 - a mirror failure is never fatal
                pass

        def _handle_fail(message: str):
            if on_fail is not None:
                on_fail(message)

        worker.done.connect(_handle_done)
        worker.failed.connect(_handle_fail)
        worker.finished.connect(_cleanup)
        self._workers.add(worker)
        worker.start()
        return worker

    def shutdown(self) -> None:
        self._push_timer.stop()
        for worker in list(self._workers):
            try:
                worker.requestInterruption()
                if not worker.wait(2000):
                    worker.terminate()
                    worker.wait(500)
            except Exception:  # noqa: BLE001
                pass
        self._workers.clear()


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
