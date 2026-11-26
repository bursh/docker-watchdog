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

# Multiple targets: one web endpoint and one private Docker-healthchecked worker.
cat > "$tmp/compose.yml" <<'YAML'
services:
  web:
    image: nginx:alpine
    ports: ["127.0.0.1::80"]
    labels:
      watchdog.enabled: "true"
      watchdog.mode: "container-restart"
      watchdog.check: "http"
  worker:
    image: alpine:3.22
    command: ["sh", "-c", "rm -f /tmp/watchdog-fail; exec sleep infinity"]
    healthcheck:
      test: ["CMD-SHELL", "test ! -f /tmp/watchdog-fail"]
      interval: 1s
      timeout: 1s
      retries: 1
    labels:
      watchdog.enabled: "true"
      watchdog.check: "healthcheck"
      watchdog.healthcheck_failures: "1"
      watchdog.mode: "container-restart"
YAML
wait_healthy() {
  local cid=$1
  for _ in {1..30}; do
    [[ $(docker inspect -f '{{.State.Health.Status}}' "$cid") == healthy ]] && return 0
    sleep 1
  done
  docker inspect "$cid" >&2
  return 1
}
"${compose[@]}" up -d
web=$("${compose[@]}" ps -q web)
worker=$("${compose[@]}" ps -q worker)
wait_healthy "$worker"
port=$("${compose[@]}" port web 80)
curl --retry 10 --retry-connrefused --retry-delay 1 --fail "http://$port/" >/dev/null
web_started=$(docker inspect -f '{{.State.StartedAt}}' "$web")
worker_started=$(docker inspect -f '{{.State.StartedAt}}' "$worker")
docker exec "$worker" touch /tmp/watchdog-fail
for _ in {1..30}; do
  [[ $(docker inspect -f '{{.State.Health.Status}}' "$worker") == unhealthy ]] && break
  sleep 1
done
test "$(docker inspect -f '{{.State.Health.Status}}' "$worker")" = unhealthy
"${run[@]}" --once
wait_healthy "$worker"
test "$(docker inspect -f '{{.State.StartedAt}}' "$web")" = "$web_started"
test "$(docker inspect -f '{{.State.StartedAt}}' "$worker")" != "$worker_started"
# Recreate just the service; do not recreate its neighbour.
"${run[@]}" --recover "$project" --mode container-recreate --container worker
new_worker=$("${compose[@]}" ps -q worker)
test "$new_worker" != "$worker"
test "$("${compose[@]}" ps -q web)" = "$web"
test "$(docker inspect -f '{{.State.StartedAt}}' "$web")" = "$web_started"
wait_healthy "$new_worker"
# Two failed targets must yield one whole-project restart, not two actions.
"${compose[@]}" stop web worker
sleep 2
"${run[@]}" --once
wait_healthy "$new_worker"
test "$(docker inspect -f '{{.State.Running}}' "$web")" = true
test "$(docker inspect -f '{{.State.StartedAt}}' "$web")" != "$web_started"
"${run[@]}" --history "$project" --json | python3 -c 'import json,sys; h=json.load(sys.stdin)["history"]; assert [(x["mode"],x["result"]) for x in h[-2:]] == [("restart","started"),("restart","completed")]'
"${compose[@]}" down
"${run[@]}" --list | (! grep -F "$project")
