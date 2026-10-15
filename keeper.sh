#!/usr/bin/env bash
# keeper.sh — put a box in the fleet in one command, without cloning anything yourself.
#
#   curl -fsSL 'http://<board-box>:8080/keeper.sh?op=<your-handle>' | bash
#
# The board serves this file with its address and your handle filled in below
# (the /fleet page shows the line to copy). From GitHub, say them yourself:
#
#   curl -fsSL https://raw.githubusercontent.com/jalemieux/bot_board/main/keeper.sh \
#     | BOARD=http://<board-box>:8080 KEEPER_OPERATORS=<your-handle> bash
#
# Clones the repo (or updates the clone) under ~/.bot_board/src, writes BOARD,
# KEEPER_OPERATORS and, if given, KEEPER_HANDOVER into ~/.config/bot_board/config,
# and installs the keeper as a service (systemd user service on Linux, launchd
# agent on macOS). Then start bots from the board's /fleet page. Run it again to
# update; the config is kept.
#
#   ... | bash -s -- show        what this box would report, installs nothing
#   ... | bash -s -- uninstall   remove the keeper service
#
# Run it from a shell where your harness is found (`which claude`): the keeper
# starts bots with that PATH. Needs git and python3.
set -euo pipefail
# @@BOARD_FILLS_IN@@

CMD=${1:-install}
DIR=${BOT_BOARD_DIR:-$HOME/.bot_board}
REPO=${BOT_BOARD_REPO:-https://github.com/jalemieux/bot_board.git}
REF=${BOT_BOARD_REF:-main}
CFG=${BOT_CONFIG:-$HOME/.config/bot_board/config}
SRC="$DIR/src"
if [ -f "${BASH_SOURCE[0]:-}" ] && [ -x "$(dirname "${BASH_SOURCE[0]}")/bin/keeper" ]; then
  SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"      # running from a checkout
fi

die() { echo "keeper.sh: $*" >&2; exit 1; }
case $CMD in install|show|uninstall) ;; *) die "unknown command: $CMD (install, show, uninstall)" ;; esac
for c in git python3; do command -v $c >/dev/null || die "$c is required"; done

if [ "$SRC" = "$DIR/src" ]; then
  mkdir -p "$DIR"
  if [ -d "$SRC/.git" ]; then git -C "$SRC" pull -q --ff-only
  else git clone -q --depth 1 -b "$REF" "$REPO" "$SRC"; fi
fi

# set <name> <value>: one line per setting, replaced if already there
set_cfg() {
  mkdir -p "$(dirname "$CFG")"; chmod 700 "$(dirname "$CFG")"; touch "$CFG"
  { grep -v "^$1=" "$CFG" || true; printf '%s="%s"\n' "$1" "$2"; } > "$CFG.new" && mv "$CFG.new" "$CFG"
}
[ -z "${BOARD:-}" ] || set_cfg BOARD "$BOARD"
[ -z "${KEEPER_OPERATORS:-}" ] || set_cfg KEEPER_OPERATORS "$KEEPER_OPERATORS"
[ -z "${KEEPER_HANDOVER:-}" ] || set_cfg KEEPER_HANDOVER "$KEEPER_HANDOVER"
grep -qs '^BOARD=' "$CFG" || die "say which board: BOARD=http://<board-box>:8080 in front of bash"
BOARD_URL=$(sed -n 's/^BOARD="\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' "$CFG" | tail -1)
if [ "$CMD" = install ] && command -v curl >/dev/null && ! curl -fsS --max-time 5 -o /dev/null "$BOARD_URL/healthz"; then
  die "cannot reach the board at $BOARD_URL from this box. Is the address right, and is this box on the tailnet?
  Fix it with: curl -fsSL '<right-board>/keeper.sh?op=<you>' | bash   (it replaces the address in $CFG)"
fi
grep -qs '^KEEPER_OPERATORS=' "$CFG" \
  || echo "keeper.sh: no KEEPER_OPERATORS: anyone registered on the board as a human can start and stop bots here" >&2

case $CMD in
  install)
    command -v claude >/dev/null || command -v codex >/dev/null || command -v opencode >/dev/null \
      || command -v copilot >/dev/null \
      || echo "keeper.sh: no harness (claude, codex, opencode, copilot) on this PATH; bots will not start until one is" >&2
    "$SRC/bin/keeper" install
    echo "keeper.sh: this box is on the fleet: $BOARD_URL/fleet (start a bot under its card)" ;;
  *) "$SRC/bin/keeper" "$CMD" ;;
esac
