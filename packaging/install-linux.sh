#!/bin/sh
# Install the portable Linux build into the current user's home. No root,
# no package manager, nothing written outside ~/.local.
#
#   ./install-linux.sh            install
#   ./install-linux.sh --uninstall  remove everything it installed
#
# Uninstalling is part of the script on purpose: a tarball that scatters
# files into ~/.local with no way back is worse than no installer.
set -eu

BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

FILES="$BIN_DIR/n64patcher-cli $BIN_DIR/n64patcher-gui \
$APP_DIR/n64patcher.desktop $ICON_DIR/n64patcher.png"

if [ "${1:-}" = "--uninstall" ]; then
    for f in $FILES; do
        [ -e "$f" ] && rm -f "$f" && echo "removed $f"
    done
    command -v update-desktop-database >/dev/null 2>&1 \
        && update-desktop-database "$APP_DIR" 2>/dev/null || true
    echo "uninstalled."
    exit 0
fi

mkdir -p "$BIN_DIR" "$APP_DIR" "$ICON_DIR"

install -m 755 "$HERE/n64patcher-cli" "$BIN_DIR/n64patcher-cli"
echo "installed $BIN_DIR/n64patcher-cli"

if [ -f "$HERE/n64patcher-gui" ]; then
    install -m 755 "$HERE/n64patcher-gui" "$BIN_DIR/n64patcher-gui"
    install -m 644 "$HERE/n64patcher.desktop" "$APP_DIR/n64patcher.desktop"
    install -m 644 "$HERE/n64patcher.png" "$ICON_DIR/n64patcher.png"
    echo "installed $BIN_DIR/n64patcher-gui and its desktop entry"
    command -v update-desktop-database >/dev/null 2>&1 \
        && update-desktop-database "$APP_DIR" 2>/dev/null || true
fi

# The verified 640x480 patches are xdelta deltas. Without xdelta3 they cannot
# be applied at all, and the tool has no correct fallback for them - so say so
# now rather than at the moment a user's patch silently does not happen.
if ! command -v xdelta3 >/dev/null 2>&1; then
    echo
    echo "NOTE: xdelta3 was not found on PATH."
    echo "      Without it the verified 640x480 patches cannot be applied."
    echo "      Debian/Ubuntu: sudo apt install xdelta3"
    echo "      Fedora:        sudo dnf install xdelta"
    echo "      Arch:          sudo pacman -S xdelta3"
fi

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        echo
        echo "NOTE: $BIN_DIR is not on your PATH. Add this to your shell rc:"
        echo "      export PATH=\"\$PATH:$BIN_DIR\""
        ;;
esac

echo
echo "Done. Run: n64patcher-cli --version"
