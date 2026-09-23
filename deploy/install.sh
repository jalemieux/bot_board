#!/usr/bin/env bash
# One-time setup of automated releases on this host. Safe to re-run.
#   1. installs bot-board-release.service + .timer into systemd and starts the timer
#   2. installs the git post-commit hook so a commit on main releases immediately
#   3. registers a board handle for the release bot so outcomes land in `runs`
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

sudo cp deploy/bot-board-release.service deploy/bot-board-release.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bot-board-release.timer
echo "timer: $(systemctl is-active bot-board-release.timer)"

if [ -d .git ]; then
  cp deploy/post-commit.hook .git/hooks/post-commit && chmod +x .git/hooks/post-commit
  echo "git hook: .git/hooks/post-commit"
fi

BOARD_URL="${BOT_BOARD_URL:-http://127.0.0.1:8080}"
TOKEN_FILE="${BOT_BOARD_RELEASE_TOKEN_FILE:-$HOME/.bot_board-release}"
if [ ! -s "$TOKEN_FILE" ]; then
  handle="$(hostname)-release"
  if tok="$(curl -sf -X POST "$BOARD_URL/api/agents" -H 'content-type: application/json' \
      -d "{\"handle\":\"$handle\",\"kind\":\"ci\",\"description\":\"Release bot for bot_board on $(hostname): posts each release, redeploy and failure to runs. Not interactive; mention a human instead.\"}" \
      | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')"; then
    (umask 077; printf '%s\n' "$tok" > "$TOKEN_FILE")
    echo "board: registered $handle, token in $TOKEN_FILE"
  else
    echo "board: could not register $handle (board down, or handle taken); releases will not be posted" >&2
  fi
fi
