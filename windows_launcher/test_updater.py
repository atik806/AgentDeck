"""Offline tests for updater.py — the unpackaged / no-binding path.

A source checkout has no `velopack` and no sibling `Update.exe`, so the whole
controller must be inert and safe. Run:

    .venv\\Scripts\\python.exe test_updater.py
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import updater
from updater import UpdateController, is_packaged, run_velopack_bootstrap

app = QApplication(sys.argv)

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok   {name}")
    else:
        _failed += 1
        print(f"  FAIL {name}")


# ---------------------------------------------------------------------------
print("[1] environment detection")
check("not frozen -> not packaged", is_packaged() is False)
check("bootstrap is a no-op (no raise)", run_velopack_bootstrap() is None)


# ---------------------------------------------------------------------------
print("[2] controller is inert without a Velopack install")
u = UpdateController()
check("disabled", u.enabled is False)
check("reason is a non-empty string", isinstance(u.unavailable_reason, str) and u.unavailable_reason)

errors = []
u.error.connect(errors.append)
ups = []
u.up_to_date.connect(lambda: ups.append(1))

u.check(silent=True)
check("silent check on a disabled controller stays quiet", errors == [] and ups == [])

u.check(silent=False)
check("loud check on a disabled controller reports the reason once", len(errors) == 1)

u.download()          # nothing pending, nothing enabled
u.apply_and_restart() # must not raise or restart anything
check("download / apply are safe no-ops", True)


# ---------------------------------------------------------------------------
print("[3] UpdateInfo adapters tolerate odd shapes")

class _Rel:
    version = "1.2.3"
    notes = "fixed a thing"

class _Info:
    target_full_release = _Rel()

check("version pulled from target_full_release.version",
      updater._release_version(_Info()) == "1.2.3")
check("notes pulled from the release object",
      updater._release_notes(_Info()) == "fixed a thing")
check("missing everything -> '?'", updater._release_version(object()) == "?")
check("missing notes -> ''", updater._release_notes(object()) == "")


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
