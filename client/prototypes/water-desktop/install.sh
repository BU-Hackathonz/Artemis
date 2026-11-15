#!/usr/bin/env bash
# Installs (or updates) the Artemis Water GNOME Shell extension for the current user and enables it.
#
#   ./install.sh            install + enable
#   ./install.sh --remove   disable + uninstall
#
# On Wayland, GNOME only discovers a newly installed extension after you log out and back in.
# Updates to an already-loaded extension also need a log out / log in to take effect.
set -euo pipefail

uuid="artemis-water@bu-hackathonz.github.io"
src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$uuid"
dest="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions/$uuid"

if [[ "${1:-}" == "--remove" ]]; then
  gnome-extensions disable "$uuid" 2>/dev/null || true
  rm -rf "$dest"
  echo "Removed $uuid."
  exit 0
fi

mkdir -p "$dest/schemas"
# Every module of the extension. The connected build has no scripted presets, so an older
# install's presets.js is removed.
rm -f "$dest/presets.js"
cp "$src"/metadata.json "$src"/*.js "$src"/stylesheet.css "$dest/"
cp "$src"/schemas/*.gschema.xml "$dest/schemas/"
glib-compile-schemas "$dest/schemas"
echo "Installed to $dest"

if gnome-extensions enable "$uuid" 2>/dev/null; then
  echo "Enabled. If nothing happens, log out and back in (GNOME on Wayland loads new extensions at login)."
else
  echo "Installed. Log out and back in, then run:  gnome-extensions enable $uuid"
fi
echo "Settings:  gnome-extensions prefs $uuid"
