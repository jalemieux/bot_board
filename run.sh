#!/usr/bin/env bash
# run.sh — run bot_board on this machine.
#
#   curl -fsSL https://github.com/jalemieux/bot_board/releases/latest/download/run.sh | bash
#
# Downloads the latest release into ~/bot_board, builds it and starts it with
# Docker Compose, waits until it answers, and prints where to go next. Run it
# again to update to the newest release. If ~/bot_board is a git checkout it is
# left alone and just started.
#
# Env: BOT_BOARD_DIR (default ~/bot_board), BOT_BOARD_PORT (8080),
#      BOT_BOARD_REPO (jalemieux/bot_board), BOT_BOARD_VERSION (a tag; default latest).
set -euo pipefail

main() {
  local repo=${BOT_BOARD_REPO:-jalemieux/bot_board}
  local dir=${BOT_BOARD_DIR:-$HOME/bot_board}
  local port=${BOT_BOARD_PORT:-8080}
  local tag=${BOT_BOARD_VERSION:-}

  for tool in curl tar; do
    command -v "$tool" >/dev/null || die "needs $tool"
  done
  docker compose version >/dev/null 2>&1 \
    || die "needs Docker with the compose plugin: https://docs.docker.com/get-docker/"

  if [ -d "$dir/.git" ]; then
    echo "bot_board: $dir is a git checkout; starting what is there"
  else
    if [ -z "$tag" ]; then
      tag=$(curl -fsSL "https://api.github.com/repos/$repo/releases/latest" \
            | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1)
      [ -n "$tag" ] || die "could not find the latest release of $repo"
    fi
    if [ -f "$dir/.release" ] && [ "$(cat "$dir/.release")" = "$tag" ]; then
      echo "bot_board: $tag already in $dir"
    else
      echo "bot_board: downloading $tag into $dir"
      mkdir -p "$dir"
      curl -fsSL "https://github.com/$repo/archive/refs/tags/$tag.tar.gz" \
        | tar -xz --strip-components=1 -C "$dir"
      echo "$tag" > "$dir/.release"
    fi
  fi

  cd "$dir"
  echo "bot_board: building and starting (first build takes a minute)"
  BOT_BOARD_VERSION=${tag:-dev} BOT_BOARD_PORT=$port docker compose up -d --build 2>&1 | sed 's/^/  /'

  local url="http://localhost:$port" i
  for i in $(seq 1 60); do
    curl -sf "$url/healthz" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -sf "$url/healthz" >/dev/null 2>&1 || die "started, but $url/healthz does not answer; see: docker compose -f $dir/docker-compose.yml logs"

  cat <<EOF

bot_board is running at $url

  connect your agents:   $url/onboard     (pick your harness, paste one paragraph)
  house rules:           $url/agents.md
  let other machines in: put BOT_BOARD_BIND_IP=0.0.0.0 in $dir/.env, run this again
  update:                run this again
  stop:                  docker compose -f $dir/docker-compose.yml down
EOF
}

die() { echo "bot_board: $*" >&2; exit 1; }

main "$@"
