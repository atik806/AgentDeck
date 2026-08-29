"""Single source of version truth for AgentDeck.

Read by:
  * ``main.py``            -- ``app.setApplicationVersion(__version__)``
  * ``setup_wizard.py``    -- the ``v{__version__}`` label in the footer
  * ``updater.py``         -- the current version to compare against a release
  * ``packaging/build.py`` -- regex-parses ``__version__`` for ``vpk pack --packVersion``

Keep this module import-cheap: **no third-party imports**. The build script
reads it with a regex precisely so it never has to import PySide6 just to learn
the version number.
"""

from __future__ import annotations

#: Bump this for every release. Plain ``MAJOR.MINOR.PATCH`` (SemVer) -- Velopack
#: and GitHub tags (``v<version>``) both expect that shape.
__version__ = "0.1.1"

#: Velopack pack id / update-feed identity. Also the install-folder name
#: (``%LOCALAPPDATA%\AgentDeck``). **Never change it** once a release is public --
#: a new id orphans every installed copy from the update feed.
APP_ID = "AgentDeck"

#: Where published releases live. Velopack's ``UpdateManager`` is handed this URL
#: and fetches ``<url>/releases.win.json`` + the ``.nupkg`` packages from it.
#: GitHub's "latest release assets" endpoint is unauthenticated, so the repo/
#: release must be public.
UPDATE_FEED_URL = "https://github.com/atik806/AgentDeck/releases/latest/download"
