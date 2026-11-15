#!/usr/bin/env bash
# infra/setup-ubuntu.sh: one-time setup of Artemis on Ubuntu (Linux). Run it once, then infra/start.sh each time.
#
#   bash infra/setup-ubuntu.sh [--yes] [--no-apt] [--no-model] [--extension-dir PATH]
#
# Steps, in order. Each one says what it does before it does it:
#   1. apt: libportaudio2 (sounddevice needs it for the microphone), curl, and the desktop tools' commands
#      gtk-launch (libgtk-3-bin) and gio (libglib2.0-bin), each only if it is missing.
#      apt-get installs system packages, so this needs sudo and asks for your password. --no-apt skips it.
#   2. uv, if it is missing: asks (y/N) before running uv's official installer. --yes answers yes.
#   3. uv sync for server/: the core's Python dependencies.
#   4. The Whisper speech models (small.en, and tiny.en for "Hey Artemis"), downloaded once with the same
#      settings the core uses, so the first push-to-talk does not stall. --no-model skips it.
#   5. The GNOME Shell extension: copied (not symlinked) into ~/.local/share/gnome-shell/extensions/<uuid>/
#      and enabled if gnome-extensions is available. Needs --extension-dir PATH; skipped without it.
#
# This script never asks for, reads, prints or stores an API key.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname -- "$SCRIPT_DIR")"
SERVER="$REPO/server"

# The Linux venv lives outside the repo. On a drive shared with Windows, server/.venv is the Windows
# venv, and uv would delete and rebuild it as a Linux one (found by David's Ubuntu Claude).
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-${XDG_DATA_HOME:-$HOME/.local/share}/artemis/venv}"

ASSUME_YES=0
NO_APT=0
NO_MODEL=0
EXT_DIR=""

usage() {
    cat <<'EOF'
Usage: bash infra/setup-ubuntu.sh [options]

  --yes                  Answer yes to the uv installer question (for scripts).
  --no-apt               Do not install apt packages (you must install libportaudio2 yourself).
  --no-model             Do not download the Whisper speech model now.
  --extension-dir PATH   Copy the GNOME Shell extension in PATH into your profile and enable it.
                         Skipped if this is not given.
  -h, --help             Show this help and exit.

Run it once. It asks before anything that changes your system except apt, which it prints first.
EOF
}

die() {
    printf '\nSetup stopped: %s\n' "$*" >&2
    exit 1
}

note() {
    printf '%s\n' "$*"
}

confirm() {
    # confirm "question": 0 for yes. Without a terminal and without --yes, the answer is no.
    local answer=""
    if (( ASSUME_YES )); then
        note "$1 (y/N) yes (--yes)"
        return 0
    fi
    if [[ ! -t 0 ]]; then
        note "$1 (y/N) no (no terminal to ask in; run again with --yes to allow it)"
        return 1
    fi
    read -r -p "$1 (y/N) " answer || true
    [[ "$answer" =~ ^[Yy]([Ee][Ss])?$ ]]
}

# ----- arguments -----

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes|-y) ASSUME_YES=1; shift ;;
        --no-apt) NO_APT=1; shift ;;
        --no-model) NO_MODEL=1; shift ;;
        --extension-dir)
            [[ $# -ge 2 ]] || die "--extension-dir needs a folder."
            EXT_DIR="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *)
            printf 'Unknown option: %s\n\n' "$1" >&2
            usage >&2
            exit 1 ;;
    esac
done

# ----- preflight -----

[[ "$(uname -s)" == Linux ]] || die "this script is for Linux. On Windows, see infra/README.md."
command -v python3 >/dev/null 2>&1 || die "python3 is not installed. Install it with: sudo apt-get install -y python3"
[[ -f "$SERVER/pyproject.toml" ]] || die "the server folder was not found at $SERVER. Run this from a complete checkout."

note "Artemis setup for Ubuntu (repository: $REPO)"
note "This script will: install apt packages (if missing), install uv (if missing, after asking),"
note "sync the core's Python dependencies, download the Whisper speech model (unless --no-model),"
note "and install the GNOME Shell extension (only with --extension-dir)."
note ""

# ----- 1. apt -----

if (( NO_APT )); then
    note "1. apt: skipped (--no-apt). libportaudio2 must already be installed for the microphone to work."
else
    missing=()
    dpkg -s libportaudio2 >/dev/null 2>&1 || missing+=(libportaudio2)
    command -v curl >/dev/null 2>&1 || missing+=(curl)
    command -v gtk-launch >/dev/null 2>&1 || missing+=(libgtk-3-bin)
    command -v gio >/dev/null 2>&1 || missing+=(libglib2.0-bin)
    if (( ${#missing[@]} == 0 )); then
        note "1. apt: libportaudio2, curl, gtk-launch and gio are already installed."
    else
        command -v apt-get >/dev/null 2>&1 || die "apt-get was not found. Install ${missing[*]} with your package manager, or pass --no-apt."
        note "1. apt: installing ${missing[*]}. libportaudio2 is the audio library sounddevice uses for the microphone."
        if [[ "$(id -u)" -eq 0 ]]; then
            note "   Running as root, so no sudo is needed."
            apt-get install -y "${missing[@]}"
        else
            note "   sudo is needed because apt-get installs system packages into /usr. sudo will ask for your password."
            command -v sudo >/dev/null 2>&1 || die "sudo is not installed. Install ${missing[*]} as root, or pass --no-apt."
            sudo apt-get install -y "${missing[@]}"
        fi
    fi
fi

# ----- 2. uv -----

if command -v uv >/dev/null 2>&1; then
    note "2. uv: found ($(uv --version))."
else
    note "2. uv: not found. uv installs and runs the Python dependencies. Its official installer puts uv in ~/.local/bin;"
    note "   it may also add that folder to your shell profile. Nothing is piped to a shell: the script is downloaded first."
    if confirm "   Install uv now with the official installer from https://astral.sh/uv/install.sh?"; then
        installer="$(mktemp)"
        trap 'rm -f -- "$installer"' EXIT
        curl -LsSf https://astral.sh/uv/install.sh -o "$installer"
        sh "$installer"
        rm -f -- "$installer"
        trap - EXIT
        PATH="$HOME/.local/bin:$PATH"
        command -v uv >/dev/null 2>&1 || die "uv was installed but is not on PATH. Open a new terminal, then run this script again."
        note "   uv installed: $(uv --version)."
    else
        die "uv is needed. Install it from https://docs.astral.sh/uv/, then run this script again."
    fi
fi

# ----- 3. Python dependencies -----

note "3. Python dependencies: uv sync for server/ (security, voice, agent and echo groups)."
uv sync --frozen --project "$SERVER" --group security --group voice --group agent --group echo

# ----- 4. Whisper model -----

if (( NO_MODEL )); then
    note "4. Speech model: skipped (--no-model). The first push-to-talk will download it."
else
    note "4. Speech model: downloading the Whisper models the core uses (ARTEMIS_WHISPER_MODEL, default small.en, and the quick wake model, default tiny.en), once."
    note "   The download is large (hundreds of MB) and needs the internet."
    (
        cd -- "$SERVER"
        uv run --frozen --project "$SERVER" --group security --group voice --group agent --group echo python -c '
import time
from faster_whisper import WhisperModel
from artemis.voice.listen import whisper_model
from artemis.voice.wake import quick_model

names = [whisper_model()]  # the same names the core loads
quick = quick_model()  # None when ARTEMIS_WAKE_QUICK_MODEL=off
if quick and quick not in names:
    names.append(quick)
for name in names:
    start = time.time()
    WhisperModel(name, device="cpu", compute_type="int8")  # the same construction as WhisperVoice
    print(f"Whisper {name} ready ({time.time() - start:.1f} s).")
'
    )
fi

# ----- 4b. Chromium for Echo Check (inspect_page) -----

if (( NO_MODEL )); then
    note "   Chromium for Echo Check: skipped with --no-model (run: uv run --frozen --project server --group echo python -m playwright install chromium)."
else
    note "   Chromium for Echo Check: playwright install chromium (once, into your home folder, not the repo)."
    ( cd -- "$SERVER" && uv run --frozen --project "$SERVER" --group security --group voice --group agent --group echo python -m playwright install chromium ) \
        || note "   Chromium could not be installed; Echo Check will say so. Everything else works."
fi

# ----- 5. GNOME Shell extension -----

if [[ -z "$EXT_DIR" ]]; then
    note "5. GNOME Shell extension: skipped. No --extension-dir was given."
    note "   (client/prototypes/water-desktop is not on main, so there is nothing to install by default.)"
elif [[ ! -f "$EXT_DIR/metadata.json" ]]; then
    note "5. GNOME Shell extension: skipped. There is no metadata.json in $EXT_DIR."
else
    uuid="$(python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["uuid"])' "$EXT_DIR/metadata.json")" \
        || die "could not read the uuid from $EXT_DIR/metadata.json."
    # A safe folder name only: no slashes, and not "." or "..", so the copy and the replace stay inside extensions/.
    [[ "$uuid" =~ ^[A-Za-z0-9][A-Za-z0-9@._-]*$ ]] || die "the extension uuid '$uuid' is not a safe folder name."
    dest="$HOME/.local/share/gnome-shell/extensions/$uuid"
    note "5. GNOME Shell extension: copying $EXT_DIR to $dest (a copy, not a link), replacing an earlier copy."
    rm -rf -- "$dest"
    mkdir -p -- "$dest"
    cp -rL -- "$EXT_DIR/." "$dest/"
    # GNOME Shell loads settings from schemas/gschemas.compiled, which is not in git. Without it the
    # extension fails to enable (State: ERROR, GLib.FileError), so compile it on every install.
    if compgen -G "$dest/schemas/*.gschema.xml" >/dev/null; then
        command -v glib-compile-schemas >/dev/null 2>&1 \
            || die "glib-compile-schemas was not found. Install it with: sudo apt-get install -y libglib2.0-bin"
        glib-compile-schemas --strict "$dest/schemas" \
            || die "glib-compile-schemas could not compile the extension's settings in $dest/schemas."
        [[ -f "$dest/schemas/gschemas.compiled" ]] \
            || die "glib-compile-schemas ran but $dest/schemas/gschemas.compiled is missing."
        note "   Compiled the extension's settings (schemas/gschemas.compiled)."
    fi
    # Record which checkout was installed, so a stale copy is easy to spot after a pull.
    commit="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    printf '%s\n' "$commit" > "$dest/installed-from"
    note "   Installed the extension from commit $commit (written to $dest/installed-from)."
    if command -v gnome-extensions >/dev/null 2>&1; then
        if gnome-extensions enable "$uuid" 2>/dev/null; then
            note "   Enabled $uuid with gnome-extensions."
        else
            note "   Could not enable $uuid yet. GNOME Shell must load it first (see the note below)."
        fi
    else
        note "   gnome-extensions is not available here, so the extension is copied but not enabled."
    fi
    if [[ "${XDG_SESSION_TYPE:-}" == wayland ]]; then
        note "   You are on Wayland: log out and log back in so GNOME Shell loads the extension, then run:"
        note "     gnome-extensions enable $uuid"
    fi
    note "   Check it loaded: gnome-extensions info $uuid   (it must say State: ACTIVE)."
    note "   Check it is current: cat $dest/installed-from   (must match: git -C $REPO rev-parse --short HEAD)."
fi

note ""
note "Setup finished. Each time you want Artemis, run:  infra/start.sh"
note "Real agent: infra/start.sh --agent claude (asks for your ANTHROPIC_API_KEY hidden, unless it is set)."
