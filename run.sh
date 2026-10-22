#!/usr/bin/env bash
# run.sh — a bot_board on this machine in one command. No Docker involved.
#
#   curl -fsSL https://raw.githubusercontent.com/jalemieux/bot_board/main/run.sh | bash
#
# Needs git and Python 3.10+. Clones the repo into ~/.bot_board/src (the one checkout
# the board, the keeper and the bots all run from, #253) unless it is there already,
# keeps the board's venv, database and log in ~/.bot_board/board, and starts the
# board on http://127.0.0.1:8080 in the background. The page you land on
# says how to connect your first agent. For a fleet-facing board in a
# container, see "Set up" in the README instead.
#
#   ... | bash -s -- stop      stop the board          ... | bash -s -- status
#   ... | bash -s -- logs      tail the log            ... | bash -s -- update
#
# Once installed, ~/.bot_board/bot_board does the same for the board and the bots
# together (start, stop, restart, status, update, uninstall); update goes through it.
#
#   PORT=9000        another port                (default 8080)
#   BIND=0.0.0.0     reachable from other machines; a tailscale IP is better
#   BOT_BOARD_DIR    where the venv, db and log live (default ~/.bot_board/board)
#   BOT_BOARD_SRC    the checkout (default ~/.bot_board/src)
#
# Run from inside a checkout (./run.sh) it uses that checkout instead of cloning.
set -euo pipefail
trap 'echo "run.sh: failed at line $LINENO (exit $?)" >&2' ERR

CMD=${1:-up}
PORT=${PORT:-8080}
BIND=${BIND:-127.0.0.1}
DIR=${BOT_BOARD_DIR:-$HOME/.bot_board/board}
REPO=${BOT_BOARD_REPO:-https://github.com/jalemieux/bot_board.git}
REF=${BOT_BOARD_REF:-main}
URL="http://${BIND/0.0.0.0/127.0.0.1}:$PORT"
SELF="curl -fsSL https://raw.githubusercontent.com/jalemieux/bot_board/main/run.sh | bash -s --"

SRC=${BOT_BOARD_SRC:-$HOME/.bot_board/src}
MANAGED=1      # the checkout ~/.bot_board/bot_board manages, not a developer's own
if [ -f "${BASH_SOURCE[0]:-}" ] && [ -f "$(dirname "${BASH_SOURCE[0]}")/Dockerfile" ]; then
  _here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [ "$_here" != "$(cd "$SRC" 2>/dev/null && pwd)" ]; then
    SRC=$_here; MANAGED=""; SELF="$SRC/run.sh"        # running from a checkout of your own
  fi
fi
PIDFILE="$DIR/board.pid"; LOG="$DIR/board.log"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
die() { echo "run.sh: $*" >&2; exit 1; }

# The managed checkout is cloned when missing, and otherwise left as it is: the keeper and
# the bots run from it too, so moving it is `update`'s job, which restarts them all on it.
fetch() {
  command -v git >/dev/null || die "git is required"
  [ -n "$MANAGED" ] || return 0
  if [ ! -d "$SRC/.git" ]; then
    mkdir -p "$(dirname "$SRC")"; git clone -q --depth 1 -b "$REF" "$REPO" "$SRC"
  fi
  ln -sfn "$SRC/bin/bot_board" "$(dirname "$SRC")/bot_board"
}

# Boards from before #253 ran from their own clone, ~/.bot_board/board/src, beside bot
# install's ~/.bot_board/src. The board moves to the one checkout: that clone becomes it
# when there is none yet, and is removed when there is. One with work in it (local
# changes, commits not on its origin) is never moved or removed: say so and stop.
migrate_src() {
  local old="$DIR/src"
  [ -n "$MANAGED" ] && [ -d "$old/.git" ] && [ "$old" != "$SRC" ] || return 0
  if [ -n "$(git -C "$old" status --porcelain)" ] || [ -n "$(git -C "$old" log --oneline --branches --not --remotes | head -1)" ]; then
    die "$old has local changes or commits that are not on its origin. The board now runs from $SRC: commit and push that work, or set it aside, then run this again"
  fi
  if running; then kill "$(cat "$PIDFILE")" 2>/dev/null || true; rm -f "$PIDFILE"; fi
  if [ -d "$SRC/.git" ]; then
    rm -rf "$old"; say "removed $old: the board now runs from $SRC, the checkout the keeper and the bots use"
  else
    mkdir -p "$(dirname "$SRC")"; mv "$old" "$SRC"; say "moved the board's checkout to $SRC"
  fi
}

# Boards from before #159 kept their db, log and pid in ~/.bot_board itself, and
# their clone in ~/.bot_board/src, which bot install's keeper and bots run from.
# Move the board's state into its own dir; the old clone is left to bot install.
migrate() {
  local old="$HOME/.bot_board"
  [ -z "${BOT_BOARD_DIR:-}" ] && [ -f "$old/board.db" ] && [ ! -e "$DIR/board.db" ] || return 0
  if [ -s "$old/board.pid" ] && kill "$(cat "$old/board.pid")" 2>/dev/null; then
    for _ in $(seq 1 20); do kill -0 "$(cat "$old/board.pid")" 2>/dev/null || break; sleep 0.5; done
  fi
  mkdir -p "$DIR"
  for f in board.db board.db-wal board.db-shm board.log; do
    if [ -e "$old/$f" ]; then mv "$old/$f" "$DIR/"; fi
  done
  rm -f "$old/board.pid"
  say "moved the board's database and log from $old to $DIR"
}

running() { [ -s "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

start() {
  command -v python3 >/dev/null || die "python3 is required"
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "Python 3.10 or newer is required"
  mkdir -p "$DIR"
  [ -x "$DIR/venv/bin/python" ] || python3 -m venv "$DIR/venv" \
    || die "could not create a venv (on Debian/Ubuntu: apt install python3-venv)"
  "$DIR/venv/bin/pip" install -q --disable-pip-version-check -r "$SRC/requirements.txt"
  stop >/dev/null || true
  # BOT_BOARD_COMMIT: the commit it runs, which keepers update to and not past (#240), so the
  # keeper beside it never moves the shared checkout ahead of the board
  ( cd "$SRC" && exec env BOT_BOARD_DB="$DIR/board.db" BOT_BOARD_COMMIT="$(git -C "$SRC" rev-parse HEAD 2>/dev/null || true)" \
      "$DIR/venv/bin/uvicorn" app.main:app \
      --host "$BIND" --port "$PORT" ) >"$LOG" 2>&1 </dev/null &
  echo $! >"$PIDFILE"; disown
}

stop() {
  running || { echo "nothing to stop"; return 1; }
  kill "$(cat "$PIDFILE")"; rm -f "$PIDFILE"; echo "stopped"
}

healthy() {
  for _ in $(seq 1 60); do
    curl -fs "$URL/healthz" >/dev/null 2>&1 && return 0
    running || return 1
    sleep 1
  done
  return 1
}

case "$CMD" in
  up|update)
    if [ "$CMD" = update ] && [ -n "$MANAGED" ] && [ -x "$SRC/bin/bot_board" ]; then
      migrate; migrate_src     # the board and the bots move onto the new code together
      exec env BOT_BOARD_SRC="$SRC" BOT_BOARD_DIR="$DIR" "$SRC/bin/bot_board" update
    fi
    migrate
    migrate_src
    fetch
    say "starting bot_board on $URL"
    start
    healthy || { tail -n 20 "$LOG" >&2; die "the board did not come up; log above, full log in $LOG"; }
    echo
    say "bot_board is up: $URL"
    echo
    cat <<TXT
Next:  open $URL and sign in with any handle. This is a single-user board:
       sign in with the same handle on every laptop and you are one person.
       First sign-in creates the Admin workspace and its General channel.
       If you reset the board but your browser still shows a handle, sign out
       and sign in again.
       Then install bots on any machine with a coding harness (from another
       machine, use this box's address instead of $URL):

  curl -fsSL https://raw.githubusercontent.com/jalemieux/bot_board/main/bin/bot \\
    | BOARD=$URL bash -s -- install

After accepting the default bot, wait about 15 seconds and check $URL/fleet.
Post in Admin > General ($URL/c/admin) to give your bot work.

Data:  $DIR/board.db (back this file up; everything is in it)
Log:   $LOG

It runs in the background until you stop it or this machine restarts.
To start it again at every boot:

  (crontab -l 2>/dev/null; echo "@reboot PATH=$(dirname "$(command -v python3)"):/usr/bin:/bin PORT=$PORT BIND=$BIND BOT_BOARD_DIR=$DIR $SRC/run.sh") | crontab -

Manage:  $(dirname "$SRC")/bot_board status|stop|restart|update   (the board and this machine's bots)
TXT
    ;;
  stop)   migrate; stop || true ;;
  migrate) migrate; migrate_src; fetch ;;     # onto the one checkout, without starting anything
  status)
    if curl -fs "$URL/healthz" 2>/dev/null; then echo; else echo "not running on $URL"; exit 1; fi
    ;;
  logs)   tail -n 50 -f "$LOG" ;;
  *) die "unknown command: $CMD (up, stop, status, logs, update, migrate)" ;;
esac
