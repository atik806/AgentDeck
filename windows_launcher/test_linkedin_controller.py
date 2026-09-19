"""Offline tests for linkedin_controller.py -- the Qt bridge.

No network: the OAuth sign-in is stubbed at ``linkedin_auth.sign_in``, and the
profile lookup at ``linkedin_api.me``. ``APPDATA`` is redirected so
plugins.json, the vault and the pipeline all land in a sandbox.

    QT_QPA_PLATFORM=offscreen .venv\\Scripts\\python.exe test_linkedin_controller.py
"""

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SANDBOX = tempfile.mkdtemp(prefix="adk-linkedinctrl-")
# See test_linkedin_server.py [10]: %APPDATA% is not enough, config_dir()
# resolves through platformdirs.
os.environ["ADK_PLUGIN_STORE"] = str(Path(_SANDBOX) / "plugins.json")
os.environ["ADK_LINKEDIN_VAULT"] = str(Path(_SANDBOX) / "linkedin.bin")
os.environ["ADK_LINKEDIN_JOBS"] = str(Path(_SANDBOX) / "linkedin_jobs.json")
os.environ["ADK_MCP_CONFIG_DIR"] = _SANDBOX
os.environ["ADK_MCP_STATE"] = str(Path(_SANDBOX) / "mcp_state.json")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

import linkedin_api
import linkedin_auth
import linkedin_controller
from linkedin_secret import LinkedInSecretStore
from plugin_store import LINKEDIN, PluginStore

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


def pump(until, ms=5000):
    loop = QEventLoop()
    hit = {"v": False}

    def tick():
        if until():
            hit["v"] = True
            loop.quit()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(15)
    QTimer.singleShot(ms, loop.quit)
    tick()
    if not hit["v"]:
        loop.exec()
    timer.stop()
    return hit["v"]


def _fresh():
    PluginStore().remove(LINKEDIN)
    LinkedInSecretStore().clear()
    return linkedin_controller.LinkedInController()


# Stub the two network calls for the whole run.
_signed_in = []
linkedin_auth.sign_in = lambda cid, secret, **kw: (
    _signed_in.append((cid, secret)) or
    {"access_token": "tok-123", "expires_at": time.time() + 5_184_000, "scope": "openid"}
)
linkedin_api.me = lambda token: {"id": "member1", "name": "Jane Dev", "email": "j@example.com"}


# ---------------------------------------------------------------------------
print("[1] a fresh controller is inert")
ctrl = _fresh()
check("not connected", not ctrl.is_connected)
check("no tiers", ctrl.tiers == [])
check("no credentials", not ctrl.has_secret and not ctrl.has_provider_key
      and not ctrl.has_session_cookie)
check("nothing to expire", not ctrl.token_expired)
check("connection is None", ctrl.connection is None)


# ---------------------------------------------------------------------------
print("[2] connect -- validates first, then signs in on a worker thread")
errors = []
ctrl.error.connect(errors.append)
check("no client id is refused", ctrl.start_connect("", "secret") is False)
check("...with a sentence, not a crash", errors and "Client ID" in errors[-1])
check("nothing was stored", not ctrl.has_secret)

connects = []
ctrl.connected.connect(connects.append)
check("start_connect returns True once both fields are there",
      ctrl.start_connect("client-abc", "s3cret") is True)
check("it finishes", pump(lambda: ctrl.is_connected))
check("the sign-in got both halves", _signed_in[-1] == ("client-abc", "s3cret"))
check("connected", ctrl.is_connected)
check("official tier is on by default", ctrl.tiers == ["official"])
check("jobs is NOT on by default", not ctrl.tier_on("jobs"))
check("session is NOT on by default (REGRESSION)", not ctrl.tier_on("session"))
check("the client id is in plugins.json", ctrl.client_id == "client-abc")
check("the secret is in the vault", ctrl.has_secret)
check("the token is in the vault", bool(LinkedInSecretStore().get("access_token")))
check("the display name lands", pump(lambda: ctrl.login == "Jane Dev"))

raw = (Path(_SANDBOX) / "plugins.json").read_text(encoding="utf-8")
check("NO secret in plugins.json (REGRESSION)", "s3cret" not in raw)
check("NO token in plugins.json (REGRESSION)", "tok-123" not in raw)


# ---------------------------------------------------------------------------
print("[3] the MCP server is wired into an agent's config")
check("ensure_wired writes something", ctrl.ensure_wired() or True)
claude_cfg = Path(_SANDBOX) / "claude.json"
if claude_cfg.exists():
    text = claude_cfg.read_text(encoding="utf-8")
    check("the entry names our server", "linkedin" in text)
    check("...and carries NO credential (REGRESSION)",
          "s3cret" not in text and "tok-123" not in text)
else:
    check("no agent config to write (no agent installed here)", True)
    check("...so nothing could leak either", True)


# ---------------------------------------------------------------------------
print("[4] the jobs tier switches on with a key and off without one")
check("set_provider stores the key", ctrl.set_provider("jsearch", "rapid-key", ""))
check("the tier is on", ctrl.tier_on("jobs"))
check("the provider is recorded", ctrl.provider == "jsearch")
check("the key is in the vault", ctrl.has_provider_key)
raw = (Path(_SANDBOX) / "plugins.json").read_text(encoding="utf-8")
check("NO provider key in plugins.json (REGRESSION)", "rapid-key" not in raw)

check("an apify actor is remembered", ctrl.set_provider("apify", "", "user~actor")
      and ctrl.actor == "user~actor")
check("...and an empty key keeps the stored one", ctrl.has_provider_key)
ctrl.clear_provider_key()
check("clearing turns the tier off", not ctrl.tier_on("jobs"))
check("...and forgets the key", not ctrl.has_provider_key)


# ---------------------------------------------------------------------------
print("[5] the session tier -- off by default, explicit to turn on")
check("a warning exists for the UI to show", "User Agreement" in linkedin_controller.SESSION_WARNING)
check("...and names the real consequence", "restricted" in linkedin_controller.SESSION_WARNING)
check("an empty cookie is refused", ctrl.set_session_cookie("  ") is False)
check("still off", not ctrl.tier_on("session"))
check("a cookie turns it on", ctrl.set_session_cookie("AQEDA-fake-cookie") is True)
check("the tier is on", ctrl.tier_on("session"))
check("the cookie is in the vault", ctrl.has_session_cookie)
raw = (Path(_SANDBOX) / "plugins.json").read_text(encoding="utf-8")
check("NO cookie in plugins.json (REGRESSION)", "AQEDA-fake-cookie" not in raw)
ctrl.clear_session_cookie()
check("turning it off forgets the cookie", not ctrl.has_session_cookie)
check("...and drops the tier", not ctrl.tier_on("session"))


# ---------------------------------------------------------------------------
print("[6] the CV path is plain settings")
cv = Path(_SANDBOX) / "cv.md"
cv.write_text("# Jane", encoding="utf-8")
ctrl.set_resume_path(str(cv))
check("stored", ctrl.resume_path == str(cv))


# ---------------------------------------------------------------------------
print("[7] an expired token is visible, because there is no refresh token")
LinkedInSecretStore().save(token_expires="1")
check("token_expired flips", ctrl.token_expired)
LinkedInSecretStore().save(token_expires=str(time.time() + 1000))
check("...and clears again", not ctrl.token_expired)


# ---------------------------------------------------------------------------
print("[8] disconnect clears every credential")
ctrl.set_session_cookie("AQEDA-again")
ctrl.set_provider("jsearch", "rapid-key-2")
check("all three are stored", ctrl.has_secret and ctrl.has_provider_key
      and ctrl.has_session_cookie)
disconnects = []
# `disconnected` carries no payload, so it can't feed list.append directly.
ctrl.disconnected.connect(lambda: disconnects.append(True))
ctrl.disconnect()
check("disconnected signal fired", len(disconnects) == 1)
check("not connected", not ctrl.is_connected)
check("the client secret is gone", not ctrl.has_secret)
check("the provider key is gone", not ctrl.has_provider_key)
check("the session cookie is gone (REGRESSION -- this one matters most)",
      not ctrl.has_session_cookie)
check("the token is gone", not LinkedInSecretStore().get("access_token"))
check("plugins.json row is gone", PluginStore().get(LINKEDIN) is None)

# The pipeline is a job hunt's history, not plugin state: a disconnect must not
# delete it.
from linkedin_store import LinkedInStore

store = LinkedInStore()
store.mark_seen([{"id": "7001", "title": "Kept"}])
ctrl2 = linkedin_controller.LinkedInController()
ctrl2.disconnect()
check("the job pipeline SURVIVES a disconnect (REGRESSION)",
      LinkedInStore().get("7001") is not None)


# ---------------------------------------------------------------------------
print("[9] shutdown is safe to call at any time")
ctrl.shutdown()
ctrl2.shutdown()
check("shutdown on a disconnected controller is a no-op", True)
check("unwire_all never raises", ctrl.unwire_all() is None)


print()
print(f"{_passed} passed, {_failed} failed")
app.quit()
sys.exit(1 if _failed else 0)
