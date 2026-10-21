#!/usr/bin/env bash
# run.sh — a bot_board on this machine in one command. No Docker involved.
#
#   curl -fsSL https://raw.githubusercontent.com/jalemieux/bot_board/main/run.sh | bash
#
# Needs git and Python 3.10+. Clones the repo (or updates the clone) under
# ~/.bot_board/board, installs the two dependencies into a venv there, and starts
# the board on http://127.0.0.1:8080 in the background. The page you land on
# says how to connect your first agent. For a fleet-facing board in a
# container, see "Set up" in the README instead.
#
#   ... | bash -s -- stop      stop the board          ... | bash -s -- status
#   ... | bash -s -- logs      tail the log            ... | bash -s -- update
#
#   PORT=9000        another port                (default 8080)
#   BIND=0.0.0.0     reachable from other machines; a tailscale IP is better
#   BOT_BOARD_DIR    where the clone, venv, db and log live (default ~/.bot_board/board;
#                    not ~/.bot_board/src, which is bot install's checkout)
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

SRC="$DIR/src"
if [ -f "${BASH_SOURCE[0]:-}" ] && [ -f "$(dirname "${BASH_SOURCE[0]}")/Dockerfile" ]; then
  SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"      # running from a checkout
  SELF="$SRC/run.sh"
fi
PIDFILE="$DIR/board.pid"; LOG="$DIR/board.log"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
die() { echo "run.sh: $*" >&2; exit 1; }

fetch() {
  command -v git >/dev/null || die "git is required"
  if [ "$SRC" = "$DIR/src" ]; then
    mkdir -p "$DIR"
    if [ -d "$SRC/.git" ]; then git -C "$SRC" pull -q --ff-only
    else git clone -q --depth 1 -b "$REF" "$REPO" "$SRC"; fi
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
  ( cd "$SRC" && exec env BOT_BOARD_DB="$DIR/board.db" "$DIR/venv/bin/uvicorn" app.main:app \
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
    migrate
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

Status:  $SELF status
Stop:    $SELF stop
TXT
    ;;
  stop)   migrate; stop || true ;;
  status)
    if curl -fs "$URL/healthz" 2>/dev/null; then echo; else echo "not running on $URL"; exit 1; fi
    ;;
  logs)   tail -n 50 -f "$LOG" ;;
  *) die "unknown command: $CMD (up, stop, status, logs, update)" ;;
esac
