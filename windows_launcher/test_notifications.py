"""AttentionNotifier -- gating, throttle, click routing. Offscreen Qt."""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from notifications import AttentionNotifier, _THROTTLE_S

app = QApplication.instance() or QApplication([])

_passed = 0
_failed = 0


def check(label: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok   {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}")


print("[1] disabled by default -- notify is a no-op")
n = AttentionNotifier()
check("starts disabled", n.enabled is False)
check("notify returns False while disabled", n.notify("p1", "hi") is False)

print("[2] configure toggles it")
n.configure(enabled=True)
check("enabled after configure", n.enabled is True)
check("first notify fires", n.notify("p1", "Waiting for you", "ws · terminal 1") is True)

print("[3] per-key throttle")
check("same key immediately -> throttled", n.notify("p1", "Waiting for you") is False)
check("different key -> fires", n.notify("p2", "Finished") is True)
n.forget("p1")
check("forget() clears the throttle", n.notify("p1", "Waiting for you") is True)

print("[4] throttle window actually elapses")
n2 = AttentionNotifier()
n2.configure(enabled=True)
n2._last["k"] = time.monotonic() - (_THROTTLE_S + 1)
check("fires again once the window passes", n2.notify("k", "x") is True)

print("[5] click routing -> activated(key)")
got = []
n3 = AttentionNotifier()
n3.activated.connect(got.append)
n3.configure(enabled=True)
n3.notify("pane-xyz", "Finished", "ws")
n3._on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
check("tray trigger emits the last key", got == ["pane-xyz"])
n3._on_tray_activated(QSystemTrayIcon.ActivationReason.Context)
check("context-menu reason does not re-emit", got == ["pane-xyz"])

print("[6] disabling hides any tray + stops firing")
n3.configure(enabled=False)
check("notify off again", n3.notify("p9", "x") is False)
n3.shutdown()

print()
print(f"{_passed} passed, {_failed} failed")
raise SystemExit(1 if _failed else 0)
