% DOCKER-WATCHDOG(1) Docker Watchdog 1.2.2
% Docker Watchdog
% November 2026

# NAME

docker-watchdog - discover, monitor and recover Docker Compose projects

# SYNOPSIS

**docker-watchdog** [**--config** *PATH*] [**--state-dir** *PATH*] [**--dry-run**] [**--once**]

**docker-watchdog** **--list** [**--table** | **--json**]

**docker-watchdog** **--status** [**--json** | **--raw**]

**docker-watchdog** **--history** *PROJECT* [**--json** | **--raw**]

**docker-watchdog** **--pause** *PROJECT* [**--duration** *TIME*]

**docker-watchdog** **--resume** *PROJECT*

**docker-watchdog** **--reset-limit** *PROJECT*

**docker-watchdog** **--recover** *PROJECT* [**--mode** *MODE*]

**docker-watchdog** **--help** | **--version**

# DESCRIPTION

Docker Watchdog is a centralized Linux service that monitors HTTP availability of Docker Compose projects on the local Docker host. It requires Python 3, Docker Engine and Docker Compose V2. The Debian package installs a systemd service; normally no separate watchdog container is needed.

Projects opt in using the container label `watchdog.enabled=true`. Only labelled Compose services are inspected for published TCP ports. Other services, including databases, are not HTTP targets, but are included in project-wide recovery. Stopped labelled containers remain discoverable; one-off Compose containers are ignored. One labelled service and one distinct published TCP port per project are supported. IPv4 and IPv6 bindings of the same port are allowed. Ambiguous targets are not recovered automatically.

The watchdog obtains the project name, working directory and Compose file list from Docker metadata. It probes the actual published host port directly, bypassing external DNS, load balancers and proxies. Wildcard IPv4/IPv6 addresses become 127.0.0.1/::1. A successful response from an available endpoint is sufficient. HTTP 2xx and 3xx count as healthy; redirects are not followed. HTTP errors and connection failures count as failed checks. HTTPS validates certificates against the binding address; the Host header does not set TLS SNI. Prefer a local HTTP health endpoint behind an AWS ALB terminating TLS.

By default, three consecutive failures at 60-second intervals trigger recovery of the entire project. One successful check resets the failure counter and recovery escalation. Recovery is followed by a 120-second cooldown. Checks are scheduled independently; worker saturation, Docker latency and HTTP timeouts can delay execution. Docker events wake discovery, with periodic polling as a fallback.

# QUICK START

Install the local package and inspect the service:

    sudo apt install ./docker-watchdog_1.2.2-1_all.deb
    sudo systemctl status docker-watchdog

Add labels to the web service in its existing Compose file. For example:

    services:
      nginx:
        image: nginx:alpine
        ports:
          - "9017:80"
        labels:
          watchdog.enabled: "true"
          watchdog.path: "/"
          watchdog.mode: "restart"

Apply the Compose changes from the project directory:

    docker compose up -d
    sudo docker-watchdog --list --table
    sudo docker-watchdog --status
    sudo journalctl -u docker-watchdog -f

Use the actual application health path if available. No manual port configuration is required. The example project name is not necessarily the directory name; use the name returned by --list.

# OPTIONS

**--help**, **-h**
: Show command-line usage and exit.

**--version**
: Print the program version and exit.

**--config** *PATH*
: Read an alternative INI configuration. Default: /etc/docker-watchdog/watchdog.conf. Applies to daemon and CLI operations. Missing files use built-in defaults.

**--state-dir** *PATH*
: Select persistent state directory; default /var/lib/docker-watchdog. Do not run two real daemons against the same Docker host with separate state directories.

**--list**
: Discover current labelled projects and print legacy JSON Lines (one JSON object per project). Does not perform a health probe or recovery. Includes project name, directory, Compose files, scheme, path, host, endpoints and mode where available.

**--table**
: With --list, display a human-readable table rather than JSON Lines.

**--status**
: Show current discovered and retained project state, including health, failure counts, mode, rate limits and last observation. It reads saved results, not a live HTTP probe. Effective settings are calculated from the CLI configuration and current labels, so restart the daemon after changing configuration.

**--json**
: With --list or --status, emit enriched JSON Lines including effective settings. With --history, emit a JSON object with dated history. Do not combine with --table or --raw.

**--raw**
: With --status or --history, emit the legacy raw state schema, including Unix timestamps. Useful for older automation.

**--history** *PROJECT*
: Display up to 500 recorded recovery events with dates, modes and results; also shows retained attempt timestamps. Older v1.1 records do not contain mode or result. Dates use the server's local timezone with UTC offset. This is not an unlimited audit log.

**--pause** *PROJECT*
: Persist a maintenance pause. Waits for the project's current recovery lock, then prevents further automated recovery, including pending up retries. Without --duration, the pause lasts until resumed.

**--duration** *TIME*
: Use with --pause. Examples: 60s, 15m, 2h, 1d. A timed pause can expire while maintenance is still in progress; prefer an indefinite pause for uncertain deployments.

**--resume** *PROJECT*
: Remove pause, reset consecutive failures and schedule monitoring again. Does not clear the recovery limit or cancel pending up. A pending operation can resume.

**--reset-limit** *PROJECT*
: Clear retained rate-limit timestamps and blocked state for the named project. Does not delete the detailed history or change the configured limit. Does not itself perform recovery.

**--recover** *PROJECT*
: Request immediate recovery of a discovered project or pending operation. Honors pause and rate limit. Ordinary manual recovery bypasses the normal HTTP threshold and scheduling; pending up takes precedence and respects its cooldown. This is a mutating operation and may interrupt service.

**--mode** *MODE*
: Select manual recovery mode with --recover: restart, down-up or force-recreate. Without it, manual recovery uses the effective project mode.

**--dry-run**
: Simulate the daemon using isolated temporary state, without executing recovery commands, hooks or webhooks. The already running systemd daemon can still perform real recovery; stop it first for a fully non-mutating experiment. Administrative mutations are rejected in dry-run. Stop the simulation with Ctrl-C.

**--once**
: Run one scheduler cycle and exit; startup grace or cooldown can defer checks. Without --dry-run, uses real persistent state and may recover projects. A concurrent real daemon using the same state directory is rejected.

# RECOVERY MODES

**restart** (default)
: `docker compose restart`. Restarts existing containers, including stopped containers. Does not restore deleted containers or apply new Compose definitions.

**force-recreate**
: `docker compose up -d --force-recreate`. Recreates containers and can restore missing containers. Writable container layers and dynamically assigned ports may change.

**down-up**
: `docker compose down` followed by `docker compose up -d`. Removes and recreates containers and project networks. The up command is persisted before down; up is attempted even if down fails. If interrupted or unsuccessful, pending up is retried under cooldown and rate-limit rules even if all labelled containers are gone.

None of these modes explicitly deletes volumes with -v. However, anonymous volumes may not be reattached after down, and writable container layers may be lost. Use named volumes or bind mounts for persistent data and maintain backups. Fixed published ports are advisable when AWS ALB targets refer to host ports. Compose may pull/build images according to the existing project configuration; the watchdog adds no explicit pull flag.

Recovery uses the Compose name, directory and file list discovered from Docker. Custom --env-file, profiles and deployment-only environment variables are not automatically reconstructed. Test these projects with the same environment as the systemd service before enabling recovery.

## Escalation

Set `recovery = restart,force-recreate,down-up` globally, or `watchdog.recovery` on a project. After an unsuccessful recovery and subsequent failed checks, the next mode is selected. The last mode repeats until healthy or limited. A healthy check resets escalation. A project-specific mode without a project-specific recovery sequence disables inherited escalation. Separate mode names with commas, without spaces.

# CONFIGURATION

The INI file /etc/docker-watchdog/watchdog.conf uses section [watchdog]. All options below are optional; the defaults are shown. Unknown keys or invalid values are rejected. Time values are integer seconds. Boolean values accept true/false, yes/no or 1/0. Restart the service after changing this file:

    sudo systemctl restart docker-watchdog

**interval = 60**
: Seconds between scheduled check starts (positive).

**failures = 3**
: Consecutive failed checks before recovery (positive).

**cooldown = 120**
: Seconds after recovery before another check or retry; zero disables the delay.

**max_restarts = 3**
: Maximum attempts per rolling window. Zero means unlimited, not disabled. Cooldown still applies. Failed recovery attempts consume the limit. Unlimited retries can repeatedly interrupt a broken application; use notifications and monitor failures.

**window = 3600**
: Positive rate-limit window in seconds. History survives daemon and host restarts. When exhausted, recovery is deferred until an attempt ages out, or the limit is reset explicitly.

**http_timeout = 5**
: Positive HTTP timeout per published address, in seconds.

**command_timeout = 180**
: Positive timeout per Docker Compose command, in seconds.

**workers = 32**
: Positive maximum number of concurrent project workers.

**mode = restart**
: Default recovery mode (see RECOVERY MODES).

**recovery =**
: Optional comma-separated recovery sequence. Empty disables escalation.

**startup_grace = 60**
: Seconds of grace after discovering a new container or changed start timestamp. Zero disables grace. Protects slow-starting running containers, not an indefinitely stopped container.

**dry_run = false**
: Simulate recovery without Docker mutations. See --dry-run.

**log_level = INFO**
: DEBUG, INFO, WARNING or ERROR.

**log_success = true**
: When false, log transitions to healthy rather than every successful HTTP check.

**summary_interval = 600**
: Seconds between summary messages; zero disables them.

**docker_events = true**
: Subscribe to Docker events to wake discovery. Periodic polling remains available.

**webhook_url =**
: Optional HTTPS endpoint receiving generic JSON recovery notifications. Empty disables notifications.

**metrics_port = 0**
: Local Prometheus HTTP port (1 to 65535); zero disables the metrics server.

**pre_hook =**, **post_hook =**
: Optional absolute executable paths to trusted administrator-managed hooks. Empty disables them.

**hook_timeout = 30**
: Positive timeout in seconds for each hook.

# DOCKER LABELS

Place labels on the single service that publishes the HTTP port, not on the database or a deploy-only label section. Apply changes with docker compose up -d. Quote YAML values as strings.

**watchdog.enabled**
: Set to "true" to opt in. Discovery includes stopped containers bearing this label.

**watchdog.path**
: HTTP request path; defaults to /. Must start with a single slash.

**watchdog.scheme**
: http (default) or https.

**watchdog.host**
: Optional HTTP Host header for virtual hosts. Does not override certificate hostname verification or TLS SNI.

**watchdog.mode**, **watchdog.recovery**, **watchdog.interval**, **watchdog.failures**, **watchdog.cooldown**, **watchdog.max_restarts**, **watchdog.window**, **watchdog.http_timeout**, **watchdog.command_timeout**, **watchdog.startup_grace**
: Override the corresponding global options. All labelled containers belonging to a project must have consistent overrides. Global logging, worker count, hooks, metrics, webhook and dry-run settings cannot be set through labels.

Example of a project with unlimited attempts and escalation:

    labels:
      watchdog.enabled: "true"
      watchdog.path: "/health"
      watchdog.host: "app.example.com"
      watchdog.recovery: "restart,force-recreate,down-up"
      watchdog.max_restarts: "0"
      watchdog.startup_grace: "180"

# MAINTENANCE AND SAFE TESTING

Use the exact project name from --list. For a planned deployment:

    sudo docker-watchdog --pause myproject
    cd /opt/myproject
    docker compose down
    docker compose pull
    docker compose up -d
    sudo docker-watchdog --resume myproject

Resume only after maintenance is complete; do not resume blindly after a failed deployment. Pauses survive daemon restarts. A manual compose down normally removes a project from discovery, while compose stop leaves it monitored and may cause recovery. Manual up restores discovery. A pending watchdog down-up operation can still recreate a removed project; pause before maintenance. Re-discovery immediately before recovery reduces races, but cannot atomically coordinate unrelated external Docker commands.

To test without actual recovery:

    sudo systemctl stop docker-watchdog
    sudo docker-watchdog --dry-run

Stop with Ctrl-C, then restart the service when ready:

    sudo systemctl start docker-watchdog

For a single simulated scheduler cycle use --dry-run --once. It may only establish startup grace rather than run a probe. Test actual recovery only on an isolated or approved staging project. Stop a labelled web service with docker compose stop SERVICE, observe consecutive failures and recovery, then verify application and database functionality. Restore manually if the test fails.

# STATUS AND OBSERVABILITY

Status values include HEALTHY (last probe succeeded), FAILED (last probe failed), STARTING (grace period), COOLDOWN (post-recovery delay), PAUSED (maintenance), BLOCKED (rate limit), PENDING-UP (unfinished recovery), REMOVED (retained but no longer discovered), CONFIG-ERROR (invalid target), and UNKNOWN (no saved observation). These are snapshots, not proof of external uptime. A dash indicates unavailable data.

Useful commands:

    sudo docker-watchdog --status
    sudo docker-watchdog --list --table
    sudo docker-watchdog --status --json
    sudo docker-watchdog --history myproject
    sudo journalctl -u docker-watchdog -n 100 --no-pager
    sudo journalctl -u docker-watchdog -f

With jq installed, inspect effective options:

    sudo docker-watchdog --status --json | jq '.effective_options'

Logs report HTTP status, endpoint, duration, exceptions, recovery mode and result, discovery changes and Docker Events connection lifecycle. A 302 response is considered healthy but may mask a failed destination. Select a health endpoint that checks application dependencies if necessary. Docker Events startup is distinct from confirmation by a received event; polling continues if the stream fails.

## Metrics

Set metrics_port = 9109 and restart the service. The metrics server listens on 127.0.0.1 only.

    curl http://127.0.0.1:9109/metrics

Metrics include docker_watchdog_failures, docker_watchdog_retained_restart_attempts, docker_watchdog_paused and docker_watchdog_last_check_timestamp_seconds. They reflect saved state, including removed projects. The retained-attempt count is not a lifetime total. Configure a local Prometheus scraper or a suitably protected relay; the endpoint has no built-in remote authentication.

## Webhooks and hooks

An HTTPS webhook receives JSON containing project, event and mode for completed or failed ordinary recovery. Use a relay to convert to Slack, Teams or Telegram formats. Webhook failures do not change the recovery result. Pending up retries do not currently invoke hooks or webhook notifications.

Pre/post hooks receive three arguments: PROJECT MODE RESULT. RESULT is empty for the pre-hook; the post-hook receives the recorded result. A failed pre-hook cancels recovery; post-hook errors are logged. Hooks run with the service's privileges (normally root). Only use trusted absolute executable paths in administrator-controlled locations. Hooks are not configurable via labels.

# TROUBLESHOOTING

No projects listed: check that Docker is running, that the service has the actual container label, and that Compose changes have been applied:

    docker ps -a --filter label=watchdog.enabled=true
    docker compose config
    sudo docker-watchdog --list

Service does not start: inspect the service and journal, verify Python, Docker Compose V2 and configuration syntax:

    systemctl status docker-watchdog --no-pager -l
    journalctl -u docker-watchdog -n 100 --no-pager
    docker compose version

HTTP failure: test the discovered endpoint locally, using the correct Host header if necessary:

    curl -v --max-time 5 http://127.0.0.1:9017/
    curl -v -H 'Host: app.example.com' http://127.0.0.1:9017/

Limit reached: inspect --status and --history. Fix the application or wait until the rolling limit expires. Use --reset-limit only after investigating the cause. If a project requires unlimited attempts, explicitly set max_restarts = 0 or its label override.

Project has multiple ports: put the watchdog label only on the web service and ensure that service has exactly one distinct published TCP port. A database port on a different unlabelled service is not included in the HTTP target.

Unexpected recovery during maintenance: use --pause before modifying containers. A pending up persists across restarts and can survive manual down; pausing suspends it but does not cancel it. For permanent removal with pending recovery, stop the daemon, remove the project's containers/labels, back up state and inspect the corresponding project JSON before removing only that project's pending state. Do not edit live state files.

# FILES

**/usr/bin/docker-watchdog**
: Executable.

**/etc/docker-watchdog/watchdog.conf**
: Administrator configuration (dpkg conffile). Upgrades may prompt about locally modified settings.

**/usr/share/doc/docker-watchdog/watchdog.conf.example**
: Fully commented configuration template; compare and merge after upgrades.

**/var/lib/docker-watchdog/**
: Persistent project JSON state, pause markers and locks. Do not edit while running. Package removal and purge deliberately retain state. Back up before manual changes.

**/lib/systemd/system/docker-watchdog.service**
: Packaged systemd unit (may resolve under /usr/lib on merged-/usr systems). A manual unit in /etc/systemd/system can override it.

**/usr/share/doc/docker-watchdog/README.md**
: Additional installation, build and APT repository documentation.

# SERVICE ADMINISTRATION

    sudo systemctl enable --now docker-watchdog
    sudo systemctl restart docker-watchdog
    sudo systemctl stop docker-watchdog
    sudo apt remove docker-watchdog

To upgrade, install the newer local DEB or use a configured trusted APT repository. The package does not require Docker installation as an APT dependency, so install Docker Engine and Compose V2 independently. Removing the watchdog does not remove Compose containers. The package is not itself signed unless distributed via a separately configured signed repository.

# SECURITY AND LIMITATIONS

Docker access is effectively privileged host access. Run only against trusted Compose projects and protect their files, labels, hooks and state directory. The watchdog does not perform external ALB health checks or update ALB target registrations. It cannot reconstruct arbitrary original deployment environment or guarantee race-free interaction with independent Compose commands. State uses wall-clock timestamps; major clock changes can affect scheduling and limits. Verify new versions on staging before enabling automated recovery in production.

# SEE ALSO

**systemctl**(1), **journalctl**(1), **apt**(8), **docker**(1), **man**(1).

Use `docker compose --help` for Compose-specific documentation and `docker-watchdog --help` for a concise option summary.
