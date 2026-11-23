#!/usr/bin/env bash
# run.sh — a bot_board on this machine in one command. No Docker involved.
#
#   curl -fsSL https://raw.githubusercontent.com/jalemieux/bot_board/main/run.sh | bash
#
# Needs git and Python 3.11+. Clones the repo into ~/.bot_board/src (the one checkout
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
DIR=${BOT_BOARD_DIR:-$HOME/.bot_board/board}
# The port and address the board was last started with are kept in $DIR/board.addr (#551), so status, stop, update and a
# restart find it without PORT in the environment. A PORT or BIND named in this run wins, and the next start keeps it.
ADDR="$DIR/board.addr"
[ -n "${PORT:-}" ] || PORT=$({ sed -n 's/^PORT=//p' "$ADDR" 2>/dev/null || true; } | tail -1)
[ -n "${BIND:-}" ] || BIND=$({ sed -n 's/^BIND=//p' "$ADDR" 2>/dev/null || true; } | tail -1)
PORT=${PORT:-8080}
BIND=${BIND:-127.0.0.1}
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

die() { echo "run.sh: $*" >&2; exit 1; }
tilde() { if [ -n "$HOME" ]; then case $1 in "$HOME"|"$HOME"/*) printf '~%s' "${1#"$HOME"}"; return ;; esac; fi; printf '%s' "$1"; }      # a path under $HOME as ~/…; no ${//} replacement, whose `\~` bash 3.2 prints with its backslash

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

# A board bound to more than this machine is said so wherever its address is printed: the address shows 127.0.0.1 for 0.0.0.0 (#551)
exposed() {
  case $BIND in 127.*|localhost|::1) return 0 ;; esac
  echo "  Bound to $BIND: anyone who can reach this machine on port $PORT can use the board. To keep it on this machine, run BIND=127.0.0.1 $SELF up"
}

running() { [ -s "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

start() {
  command -v python3 >/dev/null || die "python3 is required"
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || die "Python 3.11 or newer is required"
  mkdir -p "$DIR"; chmod 700 "$DIR"
  [ -x "$DIR/venv/bin/python" ] || python3 -m venv "$DIR/venv" \
    || die "could not create a venv (on Debian/Ubuntu: apt install python3-venv)"
  "$DIR/venv/bin/pip" install -q --disable-pip-version-check -r "$SRC/requirements.txt"
  stop >/dev/null || true
  printf 'PORT=%s\nBIND=%s\n' "$PORT" "$BIND" > "$ADDR"
  # the database holds the fleet's secrets and the log quotes requests: owner-only, old installs too
  umask 077; chmod 600 "$DIR/board.db" "$LOG" 2>/dev/null || true
  # BOT_BOARD_COMMIT: the commit it runs, which keepers update to and not past (#240), so the
  # keeper beside it never moves the shared checkout ahead of the board
  ( cd "$SRC" && exec env BOT_BOARD_DB="$DIR/board.db" BOT_BOARD_COMMIT="$(git -C "$SRC" rev-parse HEAD 2>/dev/null || true)" \
      "$DIR/venv/bin/uvicorn" app.main:app \
      --host "$BIND" --port "$PORT" ) >"$LOG" 2>&1 </dev/null &
  echo $! >"$PIDFILE"; disown
}

stop() {
  running || { echo "nothing to stop"; return 1; }
  local pid; pid=$(cat "$PIDFILE")
  kill "$pid"
  # gone before it returns: a board started next would find the port still taken, and the
  # old one, still answering, would pass for the new one
  for _ in $(seq 1 50); do kill -0 "$pid" 2>/dev/null || break; sleep 0.2; done
  rm -f "$PIDFILE"; echo "stopped"
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
    if [ "$CMD" = update ] && [ -n "$MANAGED" ] && [ -x "$SRC/bin/bot_board" ] && [ -z "${BOT_BOARD_UPDATING:-}" ]; then      # bin/bot_board update calls this with the flag set
      # the board and the bots move onto the new code together
      exec env BOT_BOARD_SRC="$SRC" BOT_BOARD_DIR="$DIR" "$SRC/bin/bot_board" update
    fi
    fetch
    start
    healthy || { tail -n 20 "$LOG" >&2; die "the board did not come up; log above, full log in $LOG"; }
    if [ -n "${BOT_BOARD_QUIET:-}" ]; then
      :      # under `bot_board install`, which prints the one block itself (#766)
    elif [ "$CMD" = update ]; then
      echo "bot_board updated to $(git -C "$SRC" rev-parse --short HEAD 2>/dev/null || echo unknown) and running at $URL"
    else
      # what the output holds is what a person needs now (#766); the rest is in the README
      cat <<TXT
bot_board is up at $URL
  Installed: the board, in $(tilde "$DIR") (data: board.db, back it up; log: board.log)
  Runs:      in the background until you stop it or restart this machine
  Manage:    $(tilde "$(dirname "$SRC")")/bot_board status|stop|restart|update

Next:   open $URL and sign in with any handle
TXT
      exposed
    fi
    ;;
  stop)   stop || true ;;
  status)
    if health=$(curl -fs "$URL/healthz" 2>/dev/null); then
      commit=$(printf '%s' "$health" | sed -n 's/.*"commit": *"\([^"]*\)".*/\1/p' | tr -cd 'A-Za-z0-9._-')
      echo "running on $URL${commit:+, commit ${commit:0:12}}"
      exposed
    else echo "not running on $URL"; exit 1; fi
    ;;
  logs)   tail -n 50 -f "$LOG" ;;
  *) die "unknown command: $CMD (up, stop, status, logs, update)" ;;
esac
