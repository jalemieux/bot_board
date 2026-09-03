#!/bin/sh
# Block until the address in BOT_BOARD_BIND_IP (from .env) exists on this host.
# The compose file publishes the port on that address, so starting before it
# exists fails with "cannot assign requested address".
set -eu
cd "$(dirname "$0")/.."
ip="$(sed -n 's/^BOT_BOARD_BIND_IP=//p' .env | tr -d '[:space:]')"
case "$ip" in ""|0.0.0.0|127.0.0.1) exit 0 ;; esac
i=0
while [ $i -lt 90 ]; do
  if ip -4 addr show | grep -q " $ip/"; then exit 0; fi
  i=$((i+1)); sleep 2
done
echo "bot_board: $ip never appeared on an interface after 180s" >&2
exit 1
