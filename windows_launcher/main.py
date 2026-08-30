#!/usr/bin/env python3
"""AgentDeck (Windows Multi-Terminal Panel) -- entry point.

Opens a single window with every terminal in it. There is no launcher dialog:
the panel comes up with the configured number of live shells already running,
so ``python main.py`` puts you straight at a prompt.

Double-clicking ``run.bat`` runs this under ``pythonw.exe``, which means the
process has no console at all. The two helpers below exist for that mode: with
nowhere to print, an unhandled exception is otherwise a program that simply
never appears, with no hint as to why.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

#: Crash reports land here, beside config.json.
_ERROR_LOG = "last-error.log"

#: Flipped once the event loop is running, so a crash report can say whether the
#: app failed to start or fell over later.
_started = False


def _ensure_streams() -> None:
    """Give the process real streams when it was started without any.

    ``pythonw.exe`` sets ``sys.stdout`` and ``sys.stderr`` to ``None``.
    ``print()`` is a documented no-op in that case, so this app's own warnings
    are safe, but anything writing to a stream *directly* -- a library, a
    warning filter, ``faulthandler`` -- raises ``AttributeError`` instead. That
    is a crash caused purely by how the app was launched; devnull costs nothing.
    """
    for name, mode in (("stdin", "r"), ("stdout", "w"), ("stderr", "w")):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, mode))


def _log_path() -> Path:
    """Where to write a crash report.

    Deliberately does not import ``config`` for the directory: this runs when
    something has already failed, and ``config`` is one of the things that could
    have. It resolves to the same folder ``config.get_config_path()`` uses.
    """
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(base) / "multi-terminal" / _ERROR_LOG


def _report_crash_to_cloud(report: str, summary: str) -> None:
    """Best-effort: if a session is stored and error reporting is on, POST the
    crash to public.app_errors. Silent on any failure -- a crash reporter that
    crashes is worse than no crash reporter.

    Runs on a short-lived daemon thread so a slow network can't hold the error
    dialog hostage; process exit may cut it short, which is fine.
    """
    def _send() -> None:
        try:
            from config import load_config
            from supabase_auth import SessionStore, rest_insert
            from version import __version__

            if not load_config().get("error_reporting", True):
                return
            session = SessionStore().load()
            if session is None:
                return
            import platform

            rest_insert(
                "app_errors",
                {
                    "user_id": session.user_id,
                    "app_version": __version__,
                    "kind": "crash",
                    "phase": "runtime" if _started else "startup",
                    "message": (summary or "AgentDeck crashed")[:4000],
                    "traceback": report[:20000],
                    "os": platform.platform(),
                },
                session.access_token,
            )
        except Exception:
            pass

    try:
        import threading

        worker = threading.Thread(target=_send, name="crash-report", daemon=True)
        worker.start()
        worker.join(8.0)
    except Exception:
        pass


def _report_fatal(exc_type, exc, tb) -> None:
    """Make an unhandled exception visible when there is no console to see it."""
    report = "".join(traceback.format_exception(exc_type, exc, tb))
    # Prints normally from a terminal; silently goes to devnull under pythonw,
    # which is what the rest of this function is for.
    print(report, file=sys.stderr)

    where = ""
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report, encoding="utf-8")
        where = f"\n\nFull report written to:\n{path}"
    except OSError:
        pass

    _report_crash_to_cloud(
        report, "".join(traceback.format_exception_only(exc_type, exc)).strip()
    )

    # MessageBoxW rather than QMessageBox: Qt is one of the things that can be
    # missing or broken at this point, and user32 never is.
    try:
        import ctypes

        mb_iconerror, mb_setforeground, mb_topmost = 0x10, 0x10000, 0x40000
        summary = "".join(traceback.format_exception_only(exc_type, exc)).strip()
        title = (
            "AgentDeck stopped unexpectedly"
            if _started
            else "AgentDeck could not start"
        )
        ctypes.windll.user32.MessageBoxW(
            None,
            f"{summary}{where}\n\n{report}"[:2000],
            title,
            mb_iconerror | mb_setforeground | mb_topmost,
        )
    except Exception:
        # A crash handler that crashes is worse than no crash handler.
        pass


_ensure_streams()
sys.excepthook = _report_fatal

# Velopack's startup hook -- run before any heavy init. Right after an update it
# finalises the install and may relaunch the process, so it must come first. A
# no-op unless this is a Velopack-installed build. (Pulls in PySide6.QtCore via
# updater, which is fine now the crash handler above is in place.)
from updater import run_velopack_bootstrap  # noqa: E402

run_velopack_bootstrap()

# Imported after the crash handler is installed, deliberately: a missing or
# half-installed PySide6 is exactly the failure the handler exists to explain,
# and it cannot explain an ImportError raised before it is in place.
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QFont, QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from agentdeck_splash import show_splash  # noqa: E402
from agents import pretrust_folder, resolve_agent  # noqa: E402
from config import load_config, save_config  # noqa: E402
from terminal_panel import TerminalPanel  # noqa: E402
from version import __version__  # noqa: E402

#: App mark, shipped beside this file (see assets/).
_ICON = Path(__file__).resolve().parent / "assets" / "icon.ico"


def _load_icon() -> QIcon:
    """The window / taskbar icon, or an empty QIcon if the file is missing."""
    return QIcon(str(_ICON)) if _ICON.exists() else QIcon()


def _set_app_user_model_id() -> None:
    """Give Windows an explicit AppUserModelID.

    Without one, a ``pythonw.exe`` process is grouped on the taskbar under the
    interpreter and shows its generic icon rather than the window's. Harmless
    everywhere else.
    """
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "AgentDeck.Panel"
        )
    except Exception:
        pass


def _persist_choices(config: dict, choices: dict) -> None:
    """Fold the wizard's picks back into config.json as the new defaults."""
    folder = choices.get("folder", "") or ""
    recent = [folder] + [
        f for f in config.get("recent_folders", [])
        if isinstance(f, str) and f != folder
    ]
    config.update(
        {
            "working_folder": folder,
            "recent_folders": recent[:8],
            "default_count": int(choices.get("count", config.get("default_count", 4))),
            "agent": choices.get("agent_key", "none"),
            "agent_command": choices.get("agent_custom", ""),
        }
    )
    try:
        save_config(config)
    except (OSError, ValueError):
        pass  # a read-only config dir must not stop the app from opening


def _apply_cloud_settings(config: dict, account) -> None:
    """Fold a freshly signed-in account's cloud-synced settings into config.

    Runs once, right after an interactive sign-in and before the wizard, so the
    wizard's defaults reflect what the user set on their other machine. A no-op
    when sync is off, offline, or the account has nothing stored yet.
    """
    try:
        cloud = account.pull_cloud_settings()
    except Exception:  # noqa: BLE001 - a failed pull just means "no cloud data yet"
        cloud = None
    if not cloud:
        return
    config.update(cloud)
    try:
        save_config(config)
    except (OSError, ValueError):
        pass


def main() -> int:
    global _started

    config = load_config()

    _set_app_user_model_id()

    app = QApplication(sys.argv)
    app.setApplicationName("AgentDeck")
    app.setApplicationVersion(__version__)
    # Deliberately no setApplicationDisplayName: Qt appends " - <display name>"
    # to every window title, which doubled up the branding
    # ("AgentDeck — <folder> - AgentDeck").
    app.setOrganizationName("multi-terminal")
    app.setWindowIcon(_load_icon())
    # A hint so any stray default-font widget matches the terminal, not the OS UI.
    app.setFont(QFont("Cascadia Mono, Consolas", 10))

    # Resolve the light/dark theme and paint the app palette before any window
    # (splash, login, wizard) is built.
    import theme

    theme.init(config)
    theme.apply_palette(app)

    # The launch animation. Plays before the wizard; --no-splash / show_splash
    # config turn it off, and it can never block startup for more than a moment.
    show_splash(
        _load_icon(),
        enabled="--no-splash" not in sys.argv and config.get("show_splash", True),
    )

    # The account sign-in window comes before the wizard. A signed-in account is
    # required to use AgentDeck: the dialog's only way forward is "Continue with
    # Google" -- anything else (Quit, the [X]) ends the process here. It is
    # skipped only once a session is already stored, or under --smoke (the
    # frozen-build check in packaging/build.py). --no-login on its own is
    # ignored, so a stray flag can't wave past the mandatory sign-in.
    from account import AccountController

    account = AccountController(config)
    _skip_login = "--smoke" in sys.argv
    if account.needs_login() and not _skip_login:
        from login_window import LoginWindow

        login = LoginWindow(account, config, icon=_load_icon())
        if login.exec() != QDialog.Accepted or not account.is_signed_in:
            return 0
        _apply_cloud_settings(config, account)

    # The setup wizard is the next front door. --no-wizard (or config.skip_wizard)
    # opens straight from saved settings, for run.bat / scripted use.
    startup = None
    if "--no-wizard" not in sys.argv and not config.get("skip_wizard", False):
        from setup_wizard import SetupWizard

        wizard = SetupWizard(config)
        wizard.setWindowIcon(_load_icon())
        if wizard.exec() != QDialog.Accepted:
            return 0
        startup = wizard.choices()
        _persist_choices(config, startup)
        account.push_cloud_settings(config)

    # If a Claude Code agent is about to auto-launch in the working folder,
    # pre-accept its "trust this folder?" prompt so it opens straight in.
    if config.get("pretrust_agent_folder", False):
        if startup is not None:
            _cmd, _folder = startup.get("agent_command", ""), startup.get("folder", "")
        else:
            _cmd = resolve_agent(config.get("agent", "none"),
                                 config.get("agent_command", ""))
            _folder = config.get("working_folder", "")
        pretrust_folder(_cmd, _folder)

    panel = TerminalPanel(config, startup=startup, account=account)
    panel.setWindowIcon(_load_icon())
    panel.show()

    # Quiet check for a newer release shortly after the window is up. Only fires
    # for a Velopack-installed build; a hit shows the "update available" dialog.
    if config.get("auto_check_updates", True) and panel.updater.enabled:
        QTimer.singleShot(1500, lambda: panel.updater.check(silent=True))

    # --smoke: used by packaging/build.py to verify a frozen build actually
    # runs. Wait for the shells to come up, then exit 0 (or 3 if none did).
    if "--smoke" in sys.argv:
        def _smoke() -> None:
            alive = any(ws.running_count() for ws in panel._workspaces)
            app.exit(0 if alive else 3)

        QTimer.singleShot(4000, _smoke)

    _started = True
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
