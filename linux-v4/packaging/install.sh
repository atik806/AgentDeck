#!/usr/bin/env bash
# Per-user installer for the AgentDeck AppImage -- the Linux equivalent of
# running AgentDeck-win-Setup.exe on Windows. AppImages are portable by
# design (no installer, nothing registered with the system), so double-
# clicking a freshly-downloaded one only ever *runs* it once -- it never
# shows up in the app launcher and doesn't survive being deleted from
# Downloads/. This script does the one-time integration:
#
#   1. copies the AppImage into ~/Applications/ (the de-facto standard
#      AppImage home directory) and marks it executable
#   2. pulls the icon the build already embeds in the AppImage (no need to
#      ship one separately -- see AgentDeck-linux.spec / build_linux.py)
#   3. writes a .desktop launcher into ~/.local/share/applications/ so
#      AgentDeck appears in the GNOME/KDE/etc. app grid like any installed
#      app, with no root and nothing outside $HOME touched
#
# Usage:
#   ./install.sh [path/to/AgentDeck.AppImage]
#
# With no argument, looks for AgentDeck.AppImage next to this script, then
# in ~/Downloads/. Safe to re-run (e.g. after downloading a newer release)
# -- it just overwrites the previous install.
#
# To uninstall: rm ~/Applications/AgentDeck.AppImage
#               rm ~/.local/share/icons/AgentDeck.png
#               rm ~/.local/share/applications/agentdeck.desktop

set -euo pipefail

APP_NAME="AgentDeck"
INSTALL_DIR="$HOME/Applications"
ICON_DIR="$HOME/.local/share/icons"
DESKTOP_DIR="$HOME/.local/share/applications"
APPIMAGE_DEST="$INSTALL_DIR/$APP_NAME.AppImage"
ICON_DEST="$ICON_DIR/$APP_NAME.png"
DESKTOP_DEST="$DESKTOP_DIR/agentdeck.desktop"

# --- find the source AppImage ----------------------------------------------
SRC="${1:-}"
if [[ -z "$SRC" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    for candidate in "$SCRIPT_DIR/$APP_NAME.AppImage" "$HOME/Downloads/$APP_NAME.AppImage"; do
        if [[ -f "$candidate" ]]; then
            SRC="$candidate"
            break
        fi
    done
fi
if [[ -z "$SRC" || ! -f "$SRC" ]]; then
    echo "error: couldn't find $APP_NAME.AppImage." >&2
    echo "       pass its path explicitly: ./install.sh /path/to/$APP_NAME.AppImage" >&2
    exit 1
fi
SRC="$(cd "$(dirname "$SRC")" && pwd)/$(basename "$SRC")"
echo "[install] using $SRC"

# --- install the AppImage itself --------------------------------------------
mkdir -p "$INSTALL_DIR" "$ICON_DIR" "$DESKTOP_DIR"
cp -f "$SRC" "$APPIMAGE_DEST"
chmod +x "$APPIMAGE_DEST"
echo "[install] copied to $APPIMAGE_DEST"

# --- pull the icon the AppImage already carries -----------------------------
# Type-2 AppImages embed their .desktop file + icon at the squashfs root;
# `--appimage-extract <pattern>` pulls just matching files instead of the
# whole ~90MB payload. Falls back to the icon shipped in the repo if the
# runtime ever changes shape.
EXTRACT_DIR="$(mktemp -d)"
trap 'rm -rf "$EXTRACT_DIR"' EXIT
(
    cd "$EXTRACT_DIR"
    "$APPIMAGE_DEST" --appimage-extract "$APP_NAME.png" >/dev/null 2>&1 || true
)
if [[ -f "$EXTRACT_DIR/squashfs-root/$APP_NAME.png" ]]; then
    cp -f "$EXTRACT_DIR/squashfs-root/$APP_NAME.png" "$ICON_DEST"
else
    REPO_ICON="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/windows_launcher/assets/icon-256.png"
    if [[ -f "$REPO_ICON" ]]; then
        cp -f "$REPO_ICON" "$ICON_DEST"
    else
        echo "[install] warning: couldn't find an icon; the launcher entry will use a generic one" >&2
    fi
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

# Refresh the desktop/icon caches so it shows up without a re-login, where
# the tools exist (some minimal DEs lack them -- harmless to skip).
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -qf "$ICON_DIR" >/dev/null 2>&1 || true

echo
echo "[install] done. $APP_NAME is installed -- open it from your app launcher"
echo "          (search \"$APP_NAME\"), or run: $APPIMAGE_DEST"
