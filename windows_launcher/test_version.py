"""Offline tests for version.py. Run:

    .venv\\Scripts\\python.exe test_version.py
"""

import re
import sys

import version

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
print("[1] __version__ is SemVer-shaped")
check("has __version__", isinstance(version.__version__, str) and version.__version__)
check(
    "MAJOR.MINOR.PATCH",
    re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version.__version__) is not None,
)

print("[2] packaging metadata present")
check("APP_ID is 'AgentDeck'", version.APP_ID == "AgentDeck")
check("feed URL is https + github", version.UPDATE_FEED_URL.startswith("https://github.com/"))
check("feed URL points at releases", "releases" in version.UPDATE_FEED_URL)

print("[3] module is import-cheap (no third-party imports)")
# version.py must be parseable/usable without PySide6 etc. It already imported
# above; assert it pulled in nothing heavy.
heavy = {"PySide6", "velopack", "numpy"}
check("no heavy modules imported by version", heavy.isdisjoint(sys.modules))

print("[4] build.py can regex-parse the version the same way")
src = open("version.py", encoding="utf-8").read()
m = re.search(r'__version__\s*=\s*"([^"]+)"', src)
check("regex finds it", m is not None and m.group(1) == version.__version__)


print()
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
