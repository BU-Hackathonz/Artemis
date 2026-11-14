#!/usr/bin/env bash
# Agent Board helper. Usage:
#   board.sh post "message"     post to the board, prefixed with your agent tag
#   board.sh new                print board comments you haven't seen yet
#   board.sh wait [seconds]     block until a new comment mentions you (default 540s)
#   board.sh all [n]            print the last n comments (default 20)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
source .board/board.env                      # BOARD_ISSUE (committed)
_ENV_TAG="${AGENT_TAG:-}"
[ -f .board/local.env ] && source .board/local.env  # AGENT_TAG, ME (gitignored)
[ -n "$_ENV_TAG" ] && AGENT_TAG="$_ENV_TAG"   # env var wins, so two agents can share one checkout
ME="${ME:-$(gh api user -q .login)}"
[ -z "${AGENT_TAG:-}" ] && AGENT_TAG="$ME-agent"
# read-state is per agent tag, so e.g. Jun's Claude and Jun's Codex don't eat each other's messages
STATE=".board/last_seen.$(echo "$AGENT_TAG" | tr -c 'A-Za-z0-9' '_')"
mkdir -p .board

fetch_new() {
  local last; last=$(cat "$STATE" 2>/dev/null || echo 0)
  gh api --paginate "repos/{owner}/{repo}/issues/$BOARD_ISSUE/comments?per_page=100" \
    --jq ".[] | select(.id > $last) | {id, user: .user.login, at: .created_at, body}" \
    | jq -s '.'
}

print_and_advance() {
  local json="$1"
  echo "$json" | jq -r '.[] | "----- #\(.id) \(.user) \(.at)\n\(.body)\n"'
  local max; max=$(echo "$json" | jq '[.[].id] | max // empty')
  [ -n "$max" ] && echo "$max" > "$STATE"
  return 0
}

case "${1:-new}" in
  post) shift; gh issue comment "$BOARD_ISSUE" --body "[$AGENT_TAG] $*" >/dev/null && echo "posted";;
  new)  json=$(fetch_new); [ "$(echo "$json" | jq length)" = 0 ] && echo "(no new messages)" || print_and_advance "$json";;
  all)  gh api "repos/{owner}/{repo}/issues/$BOARD_ISSUE/comments?per_page=${2:-20}" \
          --jq '.[] | "----- \(.user.login) \(.created_at)\n\(.body)\n"';;
  wait) deadline=$(( $(date +%s) + ${2:-540} ))
        while [ "$(date +%s)" -lt "$deadline" ]; do
          json=$(fetch_new)
          if [ "$(echo "$json" | jq length)" != 0 ]; then
            hits=$(echo "$json" | jq --arg me "@$ME" --arg tag "[$AGENT_TAG]" \
              '[.[] | select((.body | contains($me)) and ((.body | startswith($tag)) | not))] | length')
            print_and_advance "$json"
            [ "$hits" != 0 ] && { echo ">>> You were mentioned. Act on it."; exit 0; }
          fi
          sleep 60
        done
        echo "(no mentions yet, run wait again)";;
  *) echo "usage: board.sh post|new|wait|all"; exit 1;;
esac
