#!/bin/sh
# Keep this terminal running. The SSH alias may use ProxyJump through a public host.
set -eu
trap 'exit 0' INT TERM
while true; do
  ssh -NT -o BatchMode=yes -o ExitOnForwardFailure=yes -o ConnectTimeout=15 \
    -o ServerAliveInterval=20 -o ServerAliveCountMax=3 \
    -L "127.0.0.1:${PAPERAGENT_SANDBOX_LOCAL_PORT:-18080}:127.0.0.1:18080" \
    "${1:-ASUS-remote}" || true
  sleep 3
done
