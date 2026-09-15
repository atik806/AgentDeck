#!/usr/bin/env bash
# Self-extracting installer header for AgentDeck on Linux. This file is NOT
# shipped on its own -- build_linux.py appends the freshly-built AppImage
# after the __PAYLOAD_BELOW__ marker at the bottom to produce
# Releases/AgentDeck-Linux-Install.sh, the single file downloaders get from
# both GitHub Releases and vibeflow.tech/agentdeck. That's the point: one
# download, one command, installed -- no separate AppImage + install.sh
# dance, and (since it's run through an interpreter, not executed as its own
# program) no `chmod +x` needed either:
#
#   bash AgentDeck-Linux-Install.sh
#
# (`chmod +x AgentDeck-Linux-Install.sh && ./AgentDeck-Linux-Install.sh`
# works too, for anyone who prefers that.) It does the same desktop
# integration as install.sh -- see that script's header for the rationale
# -- just with the AppImage already inside instead of a separate argument:
#
#   1. extracts the embedded AppImage to ~/Applications/ and marks it
#      executable
#   2. pulls the icon the build already embeds in the AppImage
#   3. writes a .desktop launcher into ~/.local/share/applications/ so
#      AgentDeck shows up in the app grid like any installed app -- no
#      root, nothing touched outside $HOME
#
# To uninstall: rm ~/Applications/AgentDeck.AppImage
#               rm ~/.local/share/icons/AgentDeck.png
#               rm ~/.local/share/applications/agentdeck.desktop
#
# Implementation note: the payload after __PAYLOAD_BELOW__ is the raw
# AppImage's bytes, not shell -- everything above must `exit` before
# reaching it. `tail -n +N` (byte-safe, unlike e.g. `read`) pulls it back
# out. This is the same technique makeself-style installers use.

set -euo pipefail

APP_NAME="AgentDeck"
INSTALL_DIR="$HOME/Applications"
ICON_DIR="$HOME/.local/share/icons"
DESKTOP_DIR="$HOME/.local/share/applications"
APPIMAGE_DEST="$INSTALL_DIR/$APP_NAME.AppImage"
ICON_DEST="$ICON_DIR/$APP_NAME.png"
DESKTOP_DEST="$DESKTOP_DIR/agentdeck.desktop"

# $0 is reliable here (unlike a piped `curl | bash`) since downloaders save
# this to a real file before running it -- that's the whole point of it
# being one self-contained file.
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

PAYLOAD_LINE="$(awk '/^__PAYLOAD_BELOW__$/{print NR + 1; exit}' "$SELF")"
if [[ -z "${PAYLOAD_LINE:-}" ]]; then
    echo "error: corrupt installer -- no embedded AppImage found in $SELF" >&2
    echo "       (re-download it; if this keeps happening, grab the plain" >&2
    echo "       AgentDeck.AppImage + install.sh from the release instead)" >&2
    exit 1
fi

echo "[install] extracting $APP_NAME..."
mkdir -p "$INSTALL_DIR" "$ICON_DIR" "$DESKTOP_DIR"
tail -n +"$PAYLOAD_LINE" "$SELF" > "$APPIMAGE_DEST"
chmod +x "$APPIMAGE_DEST"
echo "[install] installed to $APPIMAGE_DEST"

# --- pull the icon the AppImage already carries -----------------------------
EXTRACT_DIR="$(mktemp -d)"
trap 'rm -rf "$EXTRACT_DIR"' EXIT
(
    cd "$EXTRACT_DIR"
    "$APPIMAGE_DEST" --appimage-extract "$APP_NAME.png" >/dev/null 2>&1 || true
)
if [[ -f "$EXTRACT_DIR/squashfs-root/$APP_NAME.png" ]]; then
    cp -f "$EXTRACT_DIR/squashfs-root/$APP_NAME.png" "$ICON_DEST"
else
    echo "[install] warning: couldn't extract an icon; the launcher entry will use a generic one" >&2
fi

# --- write the .desktop launcher ---------------------------------------------
cat > "$DESKTOP_DEST" <<EOF
[Desktop Entry]
Type=Application
Name=$APP_NAME
Comment=Terminal-first AI coding agent dashboard
Exec="$APPIMAGE_DEST" %U
Icon=$ICON_DEST
Terminal=false
StartupWMClass=$APP_NAME
Categories=Development;
EOF
chmod +x "$DESKTOP_DEST"
echo "[install] wrote $DESKTOP_DEST"

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -qf "$ICON_DIR" >/dev/null 2>&1 || true

echo
echo "[install] done. $APP_NAME is installed -- open it from your app launcher"
echo "          (search \"$APP_NAME\"), or run: $APPIMAGE_DEST"
exit 0
__PAYLOAD_BELOW__
