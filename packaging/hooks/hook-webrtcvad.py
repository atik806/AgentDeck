"""Override the contrib hook-webrtcvad.

`webrtcvad-wheels` installs the import module as ``webrtcvad`` but its
distribution is named ``webrtcvad-wheels``, so pyinstaller-hooks-contrib's
``hook-webrtcvad.py`` (which does ``copy_metadata('webrtcvad')``) raises and
aborts the whole build. This shadows it and collects the compiled extension the
plain way.
"""

import glob
import os
import sysconfig

binaries = []
hiddenimports = ["_webrtcvad", "webrtcvad"]

_site = sysconfig.get_paths()["purelib"]
for path in glob.glob(os.path.join(_site, "_webrtcvad*.pyd")):
    binaries.append((path, "."))
