#!/usr/bin/env bash
# infra/start.sh: starts Artemis on Ubuntu (Linux). The core runs on a free loopback port with a fresh
# launch token, and the launch info for the GNOME Shell extension is written to launch.json.
# Mirrors infra/start.ps1 (docs/contracts.md section 6). On Linux the GNOME extension is the client,
# so this script starts the core only; --core-only is kept for symmetry with start.ps1.
#
#   infra/start.sh                              # stub agent, Whisper voice
#   infra/start.sh --agent claude               # real agent; asks for ANTHROPIC_API_KEY (hidden) if unset
#   infra/start.sh --voice stub --conversation  # no microphone; built-in phrases on
#   infra/start.sh --help
#
# Safety rules, the same as start.ps1:
# - A fresh 32-byte base64url launch token per run. It goes only into the core's environment, never argv,
#   a URL or the log.
# - The core listens on 127.0.0.1 only, on a port picked free just now.
# - The Anthropic key is read with a hidden prompt (or taken from the environment), exported only into the
#   core's environment, never written to a file, a log or argv, and unset when this script exits.
# - launch.json holds the URL, the launch token and the core's PID. Never an API key. It is private
#   (directory 0700, file 0600, written with umask 077) and removed when this script exits.
# - Ctrl+C or a TERM signal stops the core's whole process group.

set -euo pipefail
set -m  # the core gets its own process group, so Ctrl+C reaches this script and it stops the core itself
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname -- "$SCRIPT_DIR")"
SERVER="$REPO/server"

# The Linux venv lives outside the repo. On a drive shared with Windows, server/.venv is the Windows
# venv, and uv would delete and rebuild it as a Linux one (found by David's Ubuntu Claude).
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-${XDG_DATA_HOME:-$HOME/.local/share}/artemis/venv}"

AGENT="stub"
VOICE="whisper"
CONVERSATION="off"
VOICE_ENGINE=""
HEALTH_TIMEOUT=120

# Set below; empty until then.
CORE_PID=""
LAUNCH_TMP=""
TOKEN=
ANTHROPIC_KEY=
WORKSPACE_ID=

usage() {
    cat <<'EOF'
Usage: infra/start.sh [options]

  --agent stub|claude           Agent for the core (default: stub). claude needs ANTHROPIC_API_KEY,
                                or a hidden prompt for it.
  --voice whisper|stub          Voice input (default: whisper). whisper needs the voice group (setup-ubuntu.sh).
  --conversation                Turn on built-in spoken phrases and follow-ups (ARTEMIS_CONVERSATION=on).
  --voice-engine elevenlabs|nvda|sapi
                                Passed as ARTEMIS_VOICE_ENGINE, if set.
  --core-only                   Accepted for symmetry with start.ps1. The core is always started alone.
  --health-timeout N            Seconds to wait for GET /health (default: 120).
  -h, --help                    Show this help.

Writes $XDG_RUNTIME_DIR/artemis/launch.json (or /tmp/artemis-$UID/launch.json) while the core runs.
Stop with Ctrl+C.
EOF
}

die() {
    printf '\nArtemis could not start: %s\n' "$*" >&2
    exit 1
}

show_core_log() {
    if [[ -s "${CORE_LOG:-/nonexistent}" ]]; then
        echo "Last lines of the core log ($CORE_LOG):"
        tail -n 20 -- "$CORE_LOG" | sed 's/^/  /'
    fi
}

# kill -0 also succeeds on an exited child that has not been reaped yet (a zombie), so read its state.
core_alive() {
    local state=""
    state="$(sed 's/^.*) //' "/proc/$CORE_PID/stat" 2>/dev/null | cut -d' ' -f1)" || true
    [[ -n "$state" && "$state" != Z && "$state" != X ]]
}

# The token is sent on stdin as a curl config line, so it never appears in curl's argv.
health_ok() {
    printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" \
        | curl -fsS --max-time 2 --config - "$URL/health" >/dev/null 2>&1
}

write_launch_file() {
    # mktemp creates the file 0600, and mv replaces launch.json in one step, so no reader sees a partial file.
    LAUNCH_TMP="$(mktemp "$LAUNCH_DIR/launch.json.XXXXXX")"
    printf '{"url": "%s", "token": "%s", "pid": %s}\n' "$URL" "$TOKEN" "$CORE_PID" >"$LAUNCH_TMP"
    mv -f -- "$LAUNCH_TMP" "$LAUNCH_FILE"
    LAUNCH_TMP=""
}

cleanup() {
    trap - EXIT INT TERM
    if [[ -n "$CORE_PID" ]]; then
        # The core is the leader of its own process group (set -m), so this stops uvicorn and anything it started.
        kill -TERM -- "-$CORE_PID" 2>/dev/null || kill -TERM "$CORE_PID" 2>/dev/null || true
        wait "$CORE_PID" 2>/dev/null || true
    fi
    if [[ -n "$LAUNCH_TMP" ]]; then
        rm -f -- "$LAUNCH_TMP"
    fi
    # Remove launch.json only if it is this run's file (a second run would have replaced it).
    if [[ -n "$CORE_PID" && -n "${LAUNCH_FILE:-}" && -f "$LAUNCH_FILE" ]] \
        && grep -q -F -- "\"pid\": $CORE_PID}" "$LAUNCH_FILE"; then
        rm -f -- "$LAUNCH_FILE"
    fi
    TOKEN=""
    ANTHROPIC_KEY=""
    WORKSPACE_ID=""
    unset ANTHROPIC_KEY WORKSPACE_ID 2>/dev/null || true
    unset ANTHROPIC_API_KEY ANTHROPIC_WORKSPACE_ID 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# ----- arguments -----

while [[ $# -gt 0 ]]; do
    case "$1" in
        --agent)
            [[ $# -ge 2 ]] || die "--agent needs stub or claude."
            AGENT="$2"; shift 2 ;;
        --voice)
            [[ $# -ge 2 ]] || die "--voice needs whisper or stub."
            VOICE="$2"; shift 2 ;;
        --voice-engine)
            [[ $# -ge 2 ]] || die "--voice-engine needs elevenlabs, nvda or sapi."
            VOICE_ENGINE="$2"; shift 2 ;;
        --conversation)
            CONVERSATION="on"; shift ;;
        --core-only)
            shift ;;
        --health-timeout)
            [[ $# -ge 2 ]] || die "--health-timeout needs a number of seconds."
            HEALTH_TIMEOUT="$2"; shift 2 ;;
        -h|--help)
            usage; exit 0 ;;
        *)
            printf 'Unknown option: %s\n\n' "$1" >&2
            usage >&2
            exit 1 ;;
    esac
done

case "$AGENT" in stub|claude) ;; *) die "--agent must be stub or claude, not '$AGENT'." ;; esac
case "$VOICE" in whisper|stub) ;; *) die "--voice must be whisper or stub, not '$VOICE'." ;; esac
case "$VOICE_ENGINE" in ""|elevenlabs|nvda|sapi) ;; *) die "--voice-engine must be elevenlabs, nvda or sapi." ;; esac
[[ "$HEALTH_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || die "--health-timeout must be a whole number of seconds above 0."

# ----- preflight -----

[[ "$(uname -s)" == Linux ]] || die "this script is for Linux. On Windows, use infra\\start.ps1."

if ! command -v uv >/dev/null 2>&1; then
    # Not on PATH: look where the official installer puts it. The user's PATH is not changed.
    for candidate in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
        if [[ -x "$candidate/uv" ]]; then
            PATH="$candidate:$PATH"
            echo "Using uv at $candidate/uv (not on PATH)."
            break
        fi
    done
fi
command -v uv >/dev/null 2>&1 \
    || die "uv is not installed or not on PATH. Run infra/setup-ubuntu.sh, or install uv from https://docs.astral.sh/uv/ and open a new terminal."
command -v python3 >/dev/null 2>&1 || die "python3 is not installed (sudo apt-get install -y python3)."
command -v curl >/dev/null 2>&1 || die "curl is not installed (sudo apt-get install -y curl)."
[[ -f "$SERVER/pyproject.toml" ]] || die "the server folder was not found at $SERVER. Run this from a complete checkout."

if [[ "$AGENT" == claude ]]; then
    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
        [[ -t 0 ]] || die "ANTHROPIC_API_KEY is not set, and --agent claude needs it. Set it, or run with --agent stub."
        read -rs -p "Anthropic API key (hidden as you type; Enter to cancel): " ANTHROPIC_KEY || true
        echo >&2
        [[ -n "$ANTHROPIC_KEY" ]] || die "no API key was entered, and --agent claude needs one. Run with --agent stub instead."
    fi
    if [[ -z "${ANTHROPIC_WORKSPACE_ID:-}" && -t 0 ]]; then
        read -rs -p "Workspace ID (only if your key needs one; Enter to skip): " WORKSPACE_ID || true
        echo >&2
    fi
fi

# ----- launch token, port and launch.json folder -----

TOKEN="$(python3 -I -c 'import base64, secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("="))')"
[[ "$TOKEN" =~ ^[A-Za-z0-9_-]{43}$ ]] || die "could not make a launch token."

PORT="$(python3 -I -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
[[ "$PORT" =~ ^[0-9]+$ ]] || die "could not find a free port on 127.0.0.1."
URL="http://127.0.0.1:$PORT"

if [[ -n "${XDG_RUNTIME_DIR:-}" ]]; then
    LAUNCH_DIR="$XDG_RUNTIME_DIR/artemis"
else
    LAUNCH_DIR="/tmp/artemis-$(id -u)"
fi
LAUNCH_FILE="$LAUNCH_DIR/launch.json"

mkdir -p -- "$LAUNCH_DIR"
[[ -d "$LAUNCH_DIR" && ! -L "$LAUNCH_DIR" ]] || die "$LAUNCH_DIR is not a plain folder. Remove it and run again."
[[ "$(stat -c %u -- "$LAUNCH_DIR")" == "$(id -u)" ]] || die "$LAUNCH_DIR belongs to another user. Remove it and run again."
chmod 700 -- "$LAUNCH_DIR"

# ----- core log -----

CORE_LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/artemis"
mkdir -p -- "$CORE_LOG_DIR"
CORE_LOG="$CORE_LOG_DIR/core.log"
: >"$CORE_LOG"

# ----- core -----

UV_GROUPS=(--group security)  # Bandit gates every write and run, also for the stub agent
[[ "$VOICE" == whisper ]] && UV_GROUPS+=(--group voice)
[[ "$AGENT" == claude ]] && UV_GROUPS+=(--group agent --group echo)  # echo: inspect_page

echo "Starting the Artemis core on $URL (agent: $AGENT, voice: $VOICE, conversation: $CONVERSATION)..."

# The secrets are exported inside this subshell only, so they are in the core's environment and nowhere else.
# They never appear on the uv command line.
(
    export ARTEMIS_TOKEN="$TOKEN" ARTEMIS_AGENT="$AGENT" ARTEMIS_VOICE="$VOICE" ARTEMIS_CONVERSATION="$CONVERSATION"
    if [[ -n "$VOICE_ENGINE" ]]; then export ARTEMIS_VOICE_ENGINE="$VOICE_ENGINE"; fi
    if [[ -n "$ANTHROPIC_KEY" ]]; then export ANTHROPIC_API_KEY="$ANTHROPIC_KEY"; fi
    if [[ -n "$WORKSPACE_ID" ]]; then export ANTHROPIC_WORKSPACE_ID="$WORKSPACE_ID"; fi
    cd -- "$SERVER"
    exec uv run --frozen --project "$SERVER" "${UV_GROUPS[@]}" uvicorn artemis.core.main:app --host 127.0.0.1 --port "$PORT"
) >>"$CORE_LOG" 2>&1 &
CORE_PID=$!

write_launch_file

# ----- wait for /health -----

ready=0
for ((i = 0; i < HEALTH_TIMEOUT * 2; i++)); do
    if ! core_alive; then
        break
    fi
    if health_ok; then
        ready=1
        break
    fi
    sleep 0.5
done

if (( ! ready )); then
    show_core_log
    if core_alive; then
        die "the core did not answer /health within $HEALTH_TIMEOUT seconds. On a first start, uv or the speech model may still be downloading: run it again, or add --voice stub to start without voice input."
    fi
    die "the core exited before it answered /health. The lines above say why."
fi

echo "Core is ready."
echo "Core: $URL. The launch info for the GNOME Shell extension is in $LAUNCH_FILE."
echo "The token is in that file and in the core's environment only. Ctrl+C to stop."

core_status=0
wait "$CORE_PID" || core_status=$?
show_core_log
echo "The core stopped while Artemis was running (exit code $core_status). The lines above say why." >&2
exit 2
