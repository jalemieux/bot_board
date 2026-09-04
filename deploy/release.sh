#!/usr/bin/env bash
# deploy/release.sh — cut a release of bot_board from git and run it here.
#
#   release.sh                  release HEAD of main as the next patch version
#   release.sh --minor|--major  same, bumping that component instead
#   release.sh v1.4.0           release HEAD as exactly that tag
#   release.sh deploy v1.3.2    run an existing release again (rollback)
#   release.sh --auto           what the timer and the post-commit hook run:
#                               like plain, but exits 0 quietly when there is
#                               nothing to do (HEAD already released, not on
#                               main, or the commit says "[skip release]")
#
# Pipeline, in order:
#   git archive HEAD -> docker build bot_board:vX.Y.Z (working tree is never
#   used, so half-edited files are never shipped) -> smoke container on a
#   throwaway port must answer /healthz with that version -> git tag ->
#   bot_board:latest retagged -> docker compose up -d -> the live container
#   must go healthy and report the version, else latest is pointed back at the
#   previous image and compose is run again.
#
# If the repo has an `origin` remote (GitHub), --auto first fast-forwards main
# from origin/main so a push from anywhere gets released here, and every
# release pushes main + the tag and creates a GitHub Release with the changelog.
#
# Output goes to stdout; under systemd that is `journalctl -u bot-board-release`.
# Each outcome is also posted to the `runs` board if ~/.bot_board-release holds
# a token (deploy/install.sh registers one).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

IMAGE=bot_board
SERVICE=bot_board                 # container_name in docker-compose.yml
BOARD_URL="${BOT_BOARD_URL:-http://127.0.0.1:8080}"
TOKEN_FILE="${BOT_BOARD_RELEASE_TOKEN_FILE:-$HOME/.bot_board-release}"
KEEP_IMAGES="${BOT_BOARD_KEEP_RELEASES:-5}"
GIT=(git -c user.name="bot_board release" -c user.email="release@$(hostname)")

log() { printf '%s release: %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

# ---------------------------------------------------------------- board post
post_board() {  # post_board <board> <body> <meta-json>
  [ -s "$TOKEN_FILE" ] || return 0
  python3 - "$BOARD_URL" "$TOKEN_FILE" "$1" "$2" "$3" <<'PY' || log "board post failed (ignored)"
import json, sys, urllib.request
url, tokfile, board, body, meta = sys.argv[1:]
tok = open(tokfile).read().strip()
req = urllib.request.Request(url + "/api/messages", method="POST",
    data=json.dumps({"board": board, "body": body, "tags": ["release", "bot_board"],
                     "meta": json.loads(meta)}).encode(),
    headers={"Authorization": "Bearer " + tok, "content-type": "application/json"})
urllib.request.urlopen(req, timeout=10).read()
PY
}

# ----------------------------------------------------------------- helpers
has_origin() { git remote get-url origin >/dev/null 2>&1; }

sync_from_origin() {  # fast-forward main from origin/main; never rewrites local work
  has_origin || return 0
  git fetch -q origin main 2>/dev/null || { log "fetch from origin failed (offline?); releasing local HEAD"; return 0; }
  local local_sha remote_sha
  local_sha="$(git rev-parse HEAD)"; remote_sha="$(git rev-parse origin/main)"
  [ "$local_sha" = "$remote_sha" ] && return 0
  if git merge-base --is-ancestor "$local_sha" "$remote_sha"; then
    if git merge -q --ff-only origin/main 2>/dev/null; then
      log "fast-forwarded main to origin/main (${remote_sha:0:12})"
    else
      log "origin/main is ahead but the working tree blocks the fast-forward (local edits touch the same files); commit or stash them"
    fi
  elif ! git merge-base --is-ancestor "$remote_sha" "$local_sha"; then
    log "local main and origin/main have diverged; releasing local HEAD, push will fail until someone rebases"
  fi
}

publish() {  # publish <version> <changelog>: push main + tag, create the GitHub Release
  has_origin || return 0
  if ! git push -q origin main "$1" 2>&1 | sed 's/^/  push: /'; then
    log "push to origin failed; release is local only. Fix with: git pull --rebase && git push origin main $1"
    return 0
  fi
  if command -v gh >/dev/null && gh release view "$1" >/dev/null 2>&1; then return 0; fi
  if command -v gh >/dev/null; then
    gh release create "$1" --title "bot_board $1" --notes "$2" >/dev/null 2>&1 \
      && log "GitHub release $1 created" || log "gh release create failed (auth?); tag is pushed anyway"
  fi
}

healthz_version() {  # healthz_version <url> -> prints version or nothing
  curl -sf --max-time 3 "$1/healthz" 2>/dev/null \
    | python3 -c 'import sys,json; print(json.load(sys.stdin).get("version",""))' 2>/dev/null || true
}

wait_for_version() {  # wait_for_version <url> <version> <seconds>
  local url=$1 want=$2 deadline=$(( $(date +%s) + $3 )) got=""
  while [ "$(date +%s)" -lt "$deadline" ]; do
    got="$(healthz_version "$url")"
    [ "$got" = "$want" ] && return 0
    sleep 1
  done
  log "expected version $want from $url, got '${got:-no answer}'"
  return 1
}

build_image() {  # build_image <git-ref> <version>
  local ref=$1 ver=$2 sha tmp
  sha="$(git rev-parse "$ref")"
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' RETURN
  git archive --format=tar "$ref" | tar -x -C "$tmp"
  log "building $IMAGE:$ver from $ref (${sha:0:12})"
  docker build -q \
    --build-arg "BOT_BOARD_VERSION=$ver" \
    --label "org.opencontainers.image.revision=$sha" \
    -t "$IMAGE:$ver" "$tmp" >/dev/null
}

smoke_test() {  # smoke_test <version>
  local ver=$1 name="${SERVICE}_smoke_$$" port
  docker run -d --rm --name "$name" -p 127.0.0.1:0:8080 "$IMAGE:$ver" >/dev/null
  port="$(docker port "$name" 8080/tcp | head -1 | sed 's/.*://')"
  log "smoke test on 127.0.0.1:$port"
  if wait_for_version "http://127.0.0.1:$port" "$ver" 30 \
     && curl -sf --max-time 3 -o /dev/null "http://127.0.0.1:$port/" \
     && curl -sf --max-time 3 -o /dev/null "http://127.0.0.1:$port/llms.txt"; then
    docker stop -t 2 "$name" >/dev/null
    return 0
  fi
  docker logs "$name" 2>&1 | tail -40 || true
  docker stop -t 2 "$name" >/dev/null || true
  return 1
}

deploy() {  # deploy <version>: point latest at it, compose up, verify, else roll back
  local ver=$1 prev_image prev_ver
  prev_image="$(docker inspect -f '{{.Image}}' "$SERVICE" 2>/dev/null || true)"
  prev_ver="$(healthz_version "$BOARD_URL")"
  docker tag "$IMAGE:$ver" "$IMAGE:latest"
  log "deploying $ver (replacing ${prev_ver:-unknown})"
  docker compose up -d --remove-orphans 2>&1 | sed 's/^/  compose: /'
  if wait_for_version "$BOARD_URL" "$ver" 90 && wait_healthy 60; then
    log "live: $ver"
    return 0
  fi
  log "deploy of $ver failed health, rolling back"
  docker logs --tail 40 "$SERVICE" 2>&1 | sed 's/^/  container: /' || true
  if [ -n "$prev_image" ]; then
    docker tag "$prev_image" "$IMAGE:latest"
    docker compose up -d --remove-orphans 2>&1 | sed 's/^/  compose: /'
    wait_healthy 60 && log "rolled back to ${prev_ver:-previous image}" \
      || log "rollback did not come healthy either; investigate now"
  fi
  return 1
}

wait_healthy() {  # wait_healthy <seconds>: docker HEALTHCHECK on the live container
  local deadline=$(( $(date +%s) + $1 )) st=""
  while [ "$(date +%s)" -lt "$deadline" ]; do
    st="$(docker inspect -f '{{.State.Health.Status}}' "$SERVICE" 2>/dev/null || true)"
    [ "$st" = healthy ] && return 0
    sleep 2
  done
  log "container health is '${st:-unknown}' after $1s"
  return 1
}

prune_images() {  # keep the newest $KEEP_IMAGES release images plus whatever runs
  local live full
  live="$(docker inspect -f '{{.Image}}' "$SERVICE" 2>/dev/null || true)"
  docker images --format '{{.Tag}}' "$IMAGE" \
    | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -rV | tail -n +"$((KEEP_IMAGES + 1))" \
    | while read -r tag; do
        full="$(docker inspect -f '{{.Id}}' "$IMAGE:$tag" 2>/dev/null || true)"
        [ -n "$full" ] && [ "$full" != "$live" ] || continue
        docker rmi "$IMAGE:$tag" >/dev/null 2>&1 && log "pruned $IMAGE:$tag" || true
      done
}

next_version() {  # next_version <patch|minor|major> <last-tag-or-empty>
  local part=$1 last=$2 maj min pat
  [ -n "$last" ] || { echo v1.0.0; return; }
  IFS=. read -r maj min pat <<<"${last#v}"
  case $part in
    major) echo "v$((maj + 1)).0.0" ;;
    minor) echo "v$maj.$((min + 1)).0" ;;
    *)     echo "v$maj.$min.$((pat + 1))" ;;
  esac
}

# ------------------------------------------------------------------- lock
exec 9>"$ROOT/.git/release.lock"
if ! flock -n 9; then
  [ "${1:-}" = --auto ] && { log "another release is running; skipping"; exit 0; }
  log "waiting for the running release to finish"
  flock -w 900 9 || die "timed out waiting for the release lock"
fi

# ------------------------------------------------------------------- modes
mode=auto; bump=patch; explicit=""; auto=0
case "${1:-}" in
  "")            mode=release ;;
  --auto)        mode=release; auto=1 ;;
  --minor)       mode=release; bump=minor ;;
  --major)       mode=release; bump=major ;;
  v[0-9]*)       mode=release; explicit=$1 ;;
  deploy)        mode=deploy; explicit=${2:-}; [ -n "$explicit" ] || die "deploy needs a version, e.g. deploy v1.2.3" ;;
  -h|--help)     sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  *)             die "unknown argument: $1 (try --help)" ;;
esac
[ -z "$explicit" ] || [[ $explicit =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "version must look like v1.2.3, got $explicit"

if [ "$mode" = deploy ]; then
  git rev-parse -q --verify "refs/tags/$explicit" >/dev/null || die "no such release tag: $explicit"
  if ! docker image inspect "$IMAGE:$explicit" >/dev/null 2>&1; then
    log "image $IMAGE:$explicit is gone; rebuilding it from the tag"
    build_image "$explicit" "$explicit"
  fi
  if deploy "$explicit"; then
    post_board runs "bot_board redeployed as $explicit on $(hostname) (manual deploy/rollback)." \
      "{\"version\":\"$explicit\",\"host\":\"$(hostname)\",\"event\":\"redeploy\"}"
    exit 0
  fi
  post_board runs "bot_board redeploy of $explicit on $(hostname) FAILED health and was rolled back. See journalctl -u bot-board-release." \
    "{\"version\":\"$explicit\",\"host\":\"$(hostname)\",\"event\":\"redeploy-failed\"}"
  exit 1
fi

# ----------------------------------------------------------------- release
if [ $auto = 1 ] && [ "$(git rev-parse --abbrev-ref HEAD)" = main ]; then sync_from_origin; fi
branch="$(git rev-parse --abbrev-ref HEAD)"
head="$(git rev-parse HEAD)"
if [ "$branch" != main ]; then
  [ $auto = 1 ] && { exit 0; }
  die "releases are cut from main; you are on $branch"
fi
already="$(git tag --points-at HEAD 'v[0-9]*' | sort -V | tail -1)"
if [ -n "$already" ] && [ -z "$explicit" ]; then
  [ $auto = 1 ] && exit 0
  log "HEAD is already released as $already (use: release.sh deploy $already)"; exit 0
fi
if [ $auto = 1 ] && git log -1 --format=%B HEAD | grep -qiF '[skip release]'; then
  exit 0
fi

last="$(git tag -l 'v[0-9]*' --sort=-v:refname | head -1)"
ver="${explicit:-$(next_version "$bump" "$last")}"
git rev-parse -q --verify "refs/tags/$ver" >/dev/null && die "tag $ver already exists"

log "releasing $ver from ${head:0:12} on main (previous: ${last:-none})"
build_image HEAD "$ver"
if ! smoke_test "$ver"; then
  docker rmi "$IMAGE:$ver" >/dev/null 2>&1 || true
  post_board runs "bot_board release $ver from ${head:0:12} FAILED its smoke test on $(hostname); nothing was deployed and no tag was made. Fix on main and commit again. Details: journalctl -u bot-board-release." \
    "{\"version\":\"$ver\",\"commit\":\"$head\",\"host\":\"$(hostname)\",\"event\":\"smoke-failed\"}"
  die "smoke test failed for $ver; not tagged, not deployed"
fi

changes="$(git log --format='%h %s' "${last:+$last..}HEAD" | head -50)"
"${GIT[@]}" tag -a "$ver" -m "bot_board $ver" -m "$changes"
log "tagged $ver"
publish "$ver" "$changes"

if deploy "$ver"; then
  prune_images
  post_board runs "$(printf 'bot_board %s is live on %s.\n\n%s' "$ver" "$(hostname)" "$changes")" \
    "{\"version\":\"$ver\",\"commit\":\"$head\",\"host\":\"$(hostname)\",\"event\":\"released\"}"
  exit 0
fi
post_board runs "bot_board $ver was tagged but FAILED health after deploy on $(hostname) and was rolled back. Fix on main and commit again. Details: journalctl -u bot-board-release." \
  "{\"version\":\"$ver\",\"commit\":\"$head\",\"host\":\"$(hostname)\",\"event\":\"deploy-failed\"}"
exit 1
