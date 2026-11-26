# Usage and operations

See the [full manual](docker-watchdog.1.md) (`man docker-watchdog` after package installation) for every CLI argument, configuration option, Docker label, recovery mode, and troubleshooting topic. [Installation](install.md) covers Debian/RPM packages, platform limitations, manual installation, upgrades, and signed repository staging. The [browser configurator](configurator.html) generates Compose labels locally without sending data to any server.

## Monitoring a web service

```yaml
labels:
  watchdog.enabled: "true"
  watchdog.check: "http"
  watchdog.path: "/health"
  watchdog.mode: "container-restart"
```

Use the real application readiness endpoint. The default is `/`. Each HTTP target must expose one unique TCP host port; IPv4 and IPv6 bindings of the same port are allowed. HTTP 2xx/3xx are accepted without following redirects. For TLS-terminating ALBs, local HTTP usually avoids certificate hostname issues. Private targets can use Docker Healthcheck with no port publication.

## Monitoring a private container

```yaml
healthcheck:
  test: ["CMD-SHELL", "test -f /tmp/ready"]
  interval: 30s
  timeout: 5s
  retries: 3
  start_period: 60s
labels:
  watchdog.enabled: "true"
  watchdog.check: "healthcheck"
  watchdog.healthcheck_failures: "1"
  watchdog.mode: "container-restart"
```

This is an example check only; make sure the application sets `/tmp/ready` appropriately or replace the check. `healthcheck_failures = 0` inherits `failures` (3 by default); `1` recovers at the first scheduled observation of Docker unhealthy/stopped. Docker's own interval/retries/start period apply before it reports unhealthy. Docker events wake discovery but do not bypass the watchdog's scheduled check interval. `starting` neither increments the failure counter nor initiates recovery. Missing healthcheck in `healthcheck`/`both` is a configuration error, not a recovery trigger. `auto` prefers Docker Healthcheck if configured, otherwise HTTP. `both` requires both checks, waiting while Docker reports `starting`.

## Coordinating recovery of several targets

The container counter key is Compose service name + replica number (`service:number`). Different targets have independent counters, methods, schedules, escalation, startup grace and container cooldown. HTTP probes within a project are processed together by its worker; other projects run in parallel. A project-wide lock serializes recovery actions and manual maintenance commands. All due probes run before selecting one action. If one target reaches its configured threshold and at least `project_failure_threshold` targets are confirmed failed, the watchdog chooses one `project_recovery_mode` action instead of individual actions. Older failed observations are reconfirmed without incrementing counters before that decision. Default threshold is 2; 0 disables promotion; default project mode is `restart`. If promotion does not apply, a ready whole-project action takes precedence over a ready container action.

Project-wide `watchdog.project_failure_threshold` and `watchdog.project_recovery_mode` labels can be provided on any monitored service; explicit values must agree. Other check/recovery labels are per target. All attempts (including container attempts) consume a shared project budget; effective mixed policies use the smallest nonzero `max_restarts` and largest `window`, with project-wide cooldown/command timeout also taking the largest effective value. To make all attempts unlimited, set `max_restarts = 0` globally with no positive overrides, or explicitly on every target. A local container cooldown does not prevent other containers recovering; the shared rate limit and a whole-project cooldown still apply. If several targets fail simultaneously during one check round, a single project action resets their counters and starts the project cooldown. There is no atomic coordination with external Compose commands; pause before maintenance.

## CLI examples

```sh
docker-watchdog --status
docker-watchdog --status --json
docker-watchdog --list --table
docker-watchdog --history example
docker-watchdog --recover example --mode container-restart --container worker
docker-watchdog --recover example --mode container-recreate --container worker:1
docker-watchdog --recover example --mode restart
docker-watchdog --pause example --duration 30m
docker-watchdog --resume example
docker-watchdog --reset-limit example
```

Container selection accepts an exact full Docker container name, ID, unique Compose service, or `service:number`. The container must be monitored. When exactly one container is monitored, `--container` is optional. For `container-recreate`, the service must have one live/stopped service container and be configured for one replica; the watchdog validates both and uses Compose `up -d --no-deps --force-recreate -- SERVICE`. It does not recreate other services. `container-restart` uses the exact Docker ID and works with replicas. `--container` cannot be used to narrow a project-wide mode.

The `--status --json` and `--list --json` project objects now include a `containers` array (per-target options, check status, counters, endpoints, Docker health status, and HTTP timing where applicable). Project-level rate policy remains at the top level. Legacy list fields remain for single-target projects; for multi-target projects, endpoints are the combined list and mode is `multiple`. `--history` includes the container name for container actions. The project-level `failures` value is the maximum target counter. Existing `--raw` state includes a new `targets` map.

## Escalation and maintenance

```yaml
labels:
  watchdog.enabled: "true"
  watchdog.check: "healthcheck"
  watchdog.recovery: "container-restart,container-recreate,restart,force-recreate,down-up"
  watchdog.max_restarts: "0"
```

A successful check resets that target's escalation. The last step repeats if failures persist. Project-wide promotion may override the next target step. If you use escalation with a scaled service, omit `container-recreate`. Manual recovery respects pause and the shared rate limit; pending-up recovery takes precedence. For maintenance use `--pause` before `docker compose down`, deploy, and `--resume` after successful deployment. Pausing waits for an active operation; a pending up is suspended, not canceled. Plan for data persistence and fixed ALB ports. See the manual for more safety details.

## Diagnostics

```sh
journalctl -u docker-watchdog --since '24 hours ago' --no-pager
journalctl -u docker-watchdog --since '24 hours ago' --no-pager | grep -Ei 'FAIL|ERROR|recovery'
curl http://127.0.0.1:9109/metrics  # if metrics_port = 9109
```

Logs include `PROJECT/SERVICE:NUMBER` for target observations. Metrics retain project gauges and add `docker_watchdog_container_failures{project,container}`. Webhooks include `container` for container-level recovery; hooks preserve the original three arguments. Pending-up retries do not call hooks/webhooks. For troubleshooting, see the [manual](docker-watchdog.1.md).
