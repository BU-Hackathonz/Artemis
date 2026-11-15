#!/usr/bin/env bash
# Water wallpaper prototype launcher for Linux/GNOME (the Windows one is Launch-WaterWallpaper.ps1).
# Reads the current GNOME wallpaper and how it's placed, writes wallpaper.js next to a temp copy of
# water.html, opens it fullscreen in Chrome/Chromium/Edge app mode, and cleans up when you press Esc.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
page="$here/water.html"
[[ -f "$page" ]] || page="$here/dist/water.html"
[[ -f "$page" ]] || { echo "water.html not found (run: npm run build:wallpaper)" >&2; exit 1; }

# --- Wallpaper (GNOME) --------------------------------------------------------------------------
scheme="$(gsettings get org.gnome.desktop.interface color-scheme 2>/dev/null || echo "'default'")"
key=picture-uri; [[ "$scheme" == "'prefer-dark'" ]] && key=picture-uri-dark
uri="$(gsettings get org.gnome.desktop.background "$key" 2>/dev/null | tr -d "'")"
[[ -z "$uri" ]] && uri="$(gsettings get org.gnome.desktop.background picture-uri 2>/dev/null | tr -d "'")"
path="$(python3 -c 'import sys,urllib.parse as u; p=u.urlparse(sys.argv[1]); print(u.unquote(p.path) if p.scheme in ("file","") else "")' "$uri")"
case "$(gsettings get org.gnome.desktop.background picture-options 2>/dev/null | tr -d "'")" in
  zoom) style=fill ;; scaled) style=fit ;; stretched) style=stretch ;; centered) style=center ;;
  wallpaper) style=tile ;; spanned) style=span ;; none) style=fill; path="" ;; *) style=fill ;;
esac
hex="$(gsettings get org.gnome.desktop.background primary-color 2>/dev/null | tr -d "'#")"
if [[ ${#hex} -eq 12 ]]; then bg="rgb($((16#${hex:0:2})),$((16#${hex:4:2})),$((16#${hex:8:2})))"
elif [[ ${#hex} -eq 6 ]]; then bg="rgb($((16#${hex:0:2})),$((16#${hex:2:2})),$((16#${hex:4:2})))"
else bg="rgb(0,0,0)"; fi

work="$(mktemp -d -t artemis-water-XXXXXX)"
trap 'rm -rf "$work"' EXIT
cp "$page" "$work/water.html"
if [[ -n "$path" && -f "$path" ]]; then
  mime="$(file -b --mime-type "$path")"
  printf "window.WALLPAPER = { src: 'data:%s;base64,%s', style: '%s', bg: '%s' };\n" \
    "$mime" "$(base64 -w0 "$path")" "$style" "$bg" > "$work/wallpaper.js"
  echo "Wallpaper: $path ($mime, fit: $style)"
else
  printf "window.WALLPAPER = { src: '', style: 'fill', bg: '%s' };\n" "$bg" > "$work/wallpaper.js"
  echo "Wallpaper: none (solid colour $bg)"
fi

# --- Browser ------------------------------------------------------------------------------------
browser=""
for b in google-chrome google-chrome-stable chromium chromium-browser microsoft-edge; do
  command -v "$b" >/dev/null && { browser="$b"; break; }
done
[[ -n "$browser" ]] || { echo "Chrome, Chromium or Edge is required." >&2; exit 1; }

echo "Opening the water preview in $browser. Press Esc in the preview to exit."
"$browser" --app="file://$work/water.html" --user-data-dir="$work/profile" \
  --no-first-run --no-default-browser-check --disable-sync --start-fullscreen 2>/dev/null
