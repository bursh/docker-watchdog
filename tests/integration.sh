#!/bin/bash
# Run only on an isolated Docker host (CI), not a production daemon.
set -euo pipefail
cd "$(dirname "$0")/.."
watchdog="$PWD/watchdog.py"
tmp=$(mktemp -d)
project="watchdogci$$"
compose=(docker compose -p "$project" -f "$tmp/compose.yml")
cleanup() { "${compose[@]}" down -v || true; rm -rf "$tmp"; }
trap cleanup EXIT
cat > "$tmp/compose.yml" <<'YAML'
services:
  web:
    image: nginx:alpine
    ports: ["127.0.0.1::80"]
    labels:
      watchdog.enabled: "true"
YAML
cat > "$tmp/config" <<'CFG'
[watchdog]
interval = 1
failures = 1
cooldown = 0
startup_grace = 0
max_restarts = 0
docker_events = false
CFG
"${compose[@]}" up -d
run=(python3 "$watchdog" --config "$tmp/config" --state-dir "$tmp/state")
for mode in restart force-recreate down-up; do
  "${compose[@]}" stop web
  "${run[@]}" --recover "$project" --mode "$mode"
  id=$("${compose[@]}" ps -q web)
  test -n "$id"
  test "$(docker inspect -f '{{.State.Running}}' "$id")" = true
  port=$("${compose[@]}" port web 80)
  curl --retry 10 --retry-connrefused --retry-delay 1 --fail "http://$port/" >/dev/null
 done
"${run[@]}" --pause "$project"
"${compose[@]}" stop web
"${run[@]}" --once
 test -z "$("${compose[@]}" ps -q web)"
"${run[@]}" --resume "$project"
"${run[@]}" --once
 test -n "$("${compose[@]}" ps -q web)"
"${compose[@]}" down
"${run[@]}" --list | (! grep -F "$project")
