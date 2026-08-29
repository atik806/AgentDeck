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
__version__ = "0.1.3"

#: Velopack pack id / update-feed identity. Also the install-folder name
#: (``%LOCALAPPDATA%\AgentDeck``). **Never change it** once a release is public --
#: a new id orphans every installed copy from the update feed.
APP_ID = "AgentDeck"

#: Where published releases live. For GitHub, this is the **plain repository
#: URL** -- Velopack's UpdateManager detects the github.com host and uses the
#: API to find the latest release and its .nupkg assets. (A
#: ``/releases/latest/download`` URL is treated as a generic web source and
#: 404s.) The repo/releases must be public: the API call is unauthenticated.
UPDATE_FEED_URL = "https://github.com/atik806/AgentDeck"
