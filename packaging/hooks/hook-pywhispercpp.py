"""PyInstaller hook for pywhispercpp.

pywhispercpp is a delvewheel build: its compiled extension imports whisper.cpp
DLLs (``ggml*.dll``, ``whisper*.dll``) plus the MSVC runtime (``msvcp140*.dll``,
``vcomp140*.dll``) that delvewheel dropped at the **site-packages root**, not
inside the package. ``collect_all('pywhispercpp')`` misses those, so the frozen
build imports fine but transcription fails at runtime.

This sweeps them in next to the extension. Confirm the target folder by looking
at ``pywhispercpp/__init__.py`` for a ``_delvewheel_...`` shim naming a private
``pywhispercpp.libs``-style directory; if present, change the ``dest`` below to
match it.
"""

import glob
import os
import sysconfig

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all("pywhispercpp")

_site = sysconfig.get_paths()["purelib"]
_patterns = (
    "ggml*.dll",
    "whisper*.dll",
    "msvcp140*.dll",
    "vcomp140*.dll",
    "_pywhispercpp*.pyd",
)

for pattern in _patterns:
    for path in glob.glob(os.path.join(_site, pattern)):
        binaries.append((path, "."))
