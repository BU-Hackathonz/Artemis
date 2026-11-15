#!/usr/bin/env bash
# Try Artemis's listener in a terminal: say "Hey Artemis, ..." (or "Artemis, ...") and watch what it hears.
# Uses the installed helper and its local Whisper model. Ctrl+C to stop. Nothing leaves this computer.
set -euo pipefail
PY="${XDG_DATA_HOME:-$HOME/.local/share}/artemis-wake/venv/bin/python"
WAKE="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions/artemis-water@bu-hackathonz.github.io/wake.py"
[[ -x "$PY" && -f "$WAKE" ]] || { echo "The listener isn't installed (run install.sh)."; exit 1; }
echo "Loading the model... then say \"Hey Artemis, open VS Code\". Pause mid-sentence after \"and\" or \"that\" to test the wait; say \"never mind\" to cancel."
start=$(date +%s.%N)
( while sleep 3600; do :; done ) | HF_HUB_OFFLINE=1 "$PY" "$WAKE" 2>/dev/null | while IFS= read -r line; do
  t=$(printf '%.1f' "$(echo "$(date +%s.%N) - $start" | bc)")
  ev=$(sed -n 's/.*"event": "\([a-z]*\)".*/\1/p' <<<"$line")
  text=$(sed -n 's/.*"text": "\(.*\)".*/\1/p' <<<"$line")
  case "$ev" in
    ready)    echo "[$t s] ready: listening for its name" ;;
    armed)    echo "[$t s] * heard its name: listening (the island would glow and chime)" ;;
    partial)  printf '\r[%s s]   ... %s\033[K' "$t" "$text" ;;
    request)  printf '\r\033[K'; echo "[$t s] > REQUEST: \"$text\"  (stopped listening on its own)" ;;
    disarmed) printf '\r\033[K'; echo "[$t s] - stopped: nothing was said after its name" ;;
    cancel)   printf '\r\033[K'; echo "[$t s] x CANCELLED (you said cancel / stop / never mind)" ;;
    error)    echo "[$t s] ! error: $line" ;;
  esac
done
