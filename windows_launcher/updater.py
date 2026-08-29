"""In-app self-update, backed by Velopack.

The whole surface the rest of the app touches:

    run_velopack_bootstrap()   -- call once, first thing in main()
    is_packaged()              -- True only for a Velopack-installed build
    UpdateController(QObject)   -- the "Update" button's controller

Design notes:

* Everything degrades to a no-op when the app is *not* a Velopack install --
  running from source (``python main.py``) or from a loosely-unzipped PyInstaller
  folder. :attr:`UpdateController.enabled` is then False and the button is hidden.
* ``import velopack`` is wrapped: a missing or broken binding disables the button
  but never stops the app.
* Velopack's ``UpdateManager`` calls block, so they run on a ``QThread``; results
  come back as Qt signals on the GUI thread.
* Velopack installs per-user to ``%LOCALAPPDATA%\\AgentDeck\\`` with no UAC, which
  is what lets the running app replace its own files.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal

from version import UPDATE_FEED_URL

try:  # pragma: no cover - import wiring
    import velopack  # type: ignore

    _VELOPACK_OK = True
    _VELOPACK_ERR = ""
except Exception as exc:  # noqa: BLE001 - any import problem disables the button
    velopack = None  # type: ignore
    _VELOPACK_OK = False
    _VELOPACK_ERR = str(exc)


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def is_packaged() -> bool:
    """True only when running as a Velopack-installed build.

    Velopack lays out ``Update.exe`` one directory above the app executable
    (``%LOCALAPPDATA%\\AgentDeck\\Update.exe`` beside ``current\\AgentDeck.exe``).
    Anything else -- source checkout, a bare ``pyinstaller`` folder -- returns
    False and the updater stays dormant.
    """
    if not getattr(sys, "frozen", False):
        return False
    try:
        return (Path(sys.executable).resolve().parent.parent / "Update.exe").is_file()
    except OSError:
        return False


def run_velopack_bootstrap() -> None:
    """Run Velopack's startup hook. Call once, as early as possible in ``main()``.

    Nearly instant on a normal launch; just after an update it runs
    first-run/cleanup work and may restart the process. No-op when the app is not
    a Velopack install or the binding is missing.
    """
    if _VELOPACK_OK and is_packaged():
        try:
            velopack.App().run()
        except Exception:  # noqa: BLE001 - never let bootstrap stop startup
            pass


# ---------------------------------------------------------------------------
# UpdateInfo adapters
# ---------------------------------------------------------------------------
#
# The young Python binding mirrors Velopack's C# model; attribute spellings may
# shift between releases, so read them tolerantly and fall back to "?".

def _release_version(info) -> str:
    for holder in ("target_full_release", "TargetFullRelease"):
        rel = getattr(info, holder, None)
        if rel is not None:
            v = getattr(rel, "version", None) or getattr(rel, "Version", None)
            if v:
                return str(v)
    v = getattr(info, "version", None) or getattr(info, "Version", None)
    return str(v) if v else "?"


def _release_notes(info) -> str:
    for src in (info, getattr(info, "target_full_release", None),
                getattr(info, "TargetFullRelease", None)):
        if src is None:
            continue
        for attr in ("release_notes", "ReleaseNotes", "notes_markdown",
                     "NotesMarkdown", "notes", "Notes"):
            n = getattr(src, attr, None)
            if n:
                return str(n)
    return ""


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class _Worker(QThread):
    """Runs one blocking UpdateManager call off the GUI thread."""

    checked = Signal(object)      # UpdateInfo | None
    progressed = Signal(int)      # 0..100
    downloaded = Signal(object)   # UpdateInfo
    failed = Signal(str)

    def __init__(self, manager, mode: str, info=None,
                 parent: Optional[QObject] = None):
        super().__init__(parent)
        self._mgr = manager
        self._mode = mode
        self._info = info

    def run(self) -> None:  # noqa: D102 - QThread entry point
        try:
            if self._mode == "check":
                self.checked.emit(self._mgr.check_for_updates())
            elif self._mode == "download":
                self._mgr.download_updates(
                    self._info,
                    progress_callback=lambda pct: self.progressed.emit(int(pct)),
                )
                self.downloaded.emit(self._info)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------

class UpdateController(QObject):
    """The in-app update surface. Safe to construct unconditionally.

    Signals (GUI thread):
        available(new_version, notes)   a newer release was found
        up_to_date()                    a non-silent check found nothing
        progress(percent)               download progress, 0..100
        ready(new_version)              downloaded, restart to apply
        error(message)                  a check/download failed
        busy_changed(bool)              a background op started / finished
    """

    available = Signal(str, str)
    up_to_date = Signal()
    progress = Signal(int)
    ready = Signal(str)
    error = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._mgr = None
        self._init_error = ""
        self._pending = None                 # UpdateInfo held check -> download
        self._worker: Optional[_Worker] = None
        self._busy = False
        self._silent = False

        if _VELOPACK_OK and is_packaged():
            try:
                self._mgr = velopack.UpdateManager(UPDATE_FEED_URL)
            except Exception as exc:  # noqa: BLE001
                self._mgr = None
                self._init_error = str(exc)

    # -- state -----------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Whether updates can actually run (Velopack install + working binding)."""
        return self._mgr is not None

    @property
    def unavailable_reason(self) -> str:
        if self._mgr is not None:
            return ""
        if not _VELOPACK_OK:
            return f"update client unavailable ({_VELOPACK_ERR})"
        if not is_packaged():
            return "updates are managed by the installed build"
        return self._init_error or "update manager could not start"

    def _set_busy(self, value: bool) -> None:
        if value != self._busy:
            self._busy = value
            self.busy_changed.emit(value)

    def _spawn_worker(self, mode: str, info=None) -> "_Worker":
        """Create the worker for one op and make sure it is disposed on finish.

        Without the ``finished`` -> ``deleteLater`` hook every check/download
        would leave its ``QThread`` object parented to this controller for the
        life of the process.
        """
        worker = _Worker(self._mgr, mode, info=info, parent=self)

        def _reap() -> None:
            worker.deleteLater()
            if self._worker is worker:
                self._worker = None

        worker.finished.connect(_reap)
        self._worker = worker
        return worker

    # -- operations ----------------------------------------------------------

    def check(self, *, silent: bool = False) -> None:
        """Look for a newer release. ``silent`` suppresses the up-to-date signal
        (used for the automatic check on launch)."""
        if self._mgr is None:
            if not silent:
                self.error.emit(self.unavailable_reason)
            return
        if self._busy:
            return
        self._silent = silent
        self._set_busy(True)
        self._worker = _Worker(self._mgr, "check", parent=self)
        self._worker.checked.connect(self._on_checked)
        self._worker.failed.connect(self._on_check_failed)
        self._worker.start()

    def _on_checked(self, info) -> None:
        self._set_busy(False)
        if info is None:
            if not self._silent:
                self.up_to_date.emit()
            return
        self._pending = info
        self.available.emit(_release_version(info), _release_notes(info))

    def download(self) -> None:
        """Download the release found by the last :meth:`check`."""
        if self._mgr is None or self._pending is None or self._busy:
            return
        self._set_busy(True)
        self._worker = _Worker(self._mgr, "download", info=self._pending, parent=self)
        self._worker.progressed.connect(self.progress)
        self._worker.downloaded.connect(self._on_downloaded)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_downloaded(self, info) -> None:
        self._set_busy(False)
        self.ready.emit(_release_version(info))

    def apply_and_restart(self) -> None:
        """Swap in the downloaded version and relaunch. Does not return on success
        -- Velopack replaces the process. The caller must shut its shells down first.
        """
        if self._mgr is None or self._pending is None:
            return
        try:
            self._mgr.apply_updates_and_restart(self._pending)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))

    def _on_check_failed(self, message: str) -> None:
        # A background launch check that can't reach GitHub (offline, no releases
        # yet, rate-limited) must not nag. A user-clicked check always reports.
        self._set_busy(False)
        if not self._silent:
            self.error.emit(message)

    def _on_failed(self, message: str) -> None:
        self._set_busy(False)
        self.error.emit(message)
