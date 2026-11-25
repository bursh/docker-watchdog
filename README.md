# Docker Watchdog 1.1.0

Linux Debian/Ubuntu, existing Docker Engine and Compose V2, root-managed trusted Compose files. Install `sudo apt install ./docker-watchdog_1.1.0-1_all.deb`. If migrating from a manual install remove its overriding `/etc/systemd/system/docker-watchdog.service` after checking it, then daemon-reload and restart. `systemctl status docker-watchdog`; `journalctl -u docker-watchdog -f`.

Add labels to the single service exposing a TCP port:

```yaml
labels:
  watchdog.enabled: "true"
  watchdog.path: "/health"
  watchdog.scheme: "http"
  watchdog.mode: "force-recreate"
  watchdog.interval: "60"
  watchdog.failures: "3"
```

Apply with `docker compose up -d`. Global defaults in `/etc/docker-watchdog/watchdog.conf` (dpkg conffile); restart watchdog after changes. Labels override defaults: mode, interval, failures, cooldown, max_restarts, window, http_timeout, command_timeout. `watchdog.host` sets HTTP Host header, not TLS SNI. Checks use published binding addresses, no proxy, no redirect follow, 2xx/3xx healthy. IPv4/IPv6 wildcard maps to loopback. HTTPS validates the certificate against the binding address. Prefer HTTP behind ALB. One unique published TCP port and one labelled service per project; replicas with different ports are unsupported. Discovery polls every 5 seconds; checks are scheduled per project, subject to Docker and worker latency.

Modes: `restart` (default, existing containers); `down-up` (down then up -d, even if down fails); `force-recreate` (up -d --force-recreate). No volume deletion or image pull flags. Anonymous volumes may be detached by down; container writable layers and dynamic host ports can change in recreate modes. ALB registration must remain correct. Compose project name, working directory and config files come from Docker labels. Custom CLI env files/profiles/environment are not recovered; verify manually with the same systemd environment. Do not use with untrusted Compose files. Intentionally stopped labelled containers count as unhealthy. Stop watchdog before maintenance; removing labels alone does not cancel a pending down-up recovery.

`sudo docker-watchdog --list` prints discovered targets as JSON lines without changing state. `sudo docker-watchdog --dry-run` continuously simulates using isolated temporary counters, can coexist with production (stop production if you want no real recovery). Ctrl-C to stop. `--dry-run --once` performs one check only. `--once` without dry-run uses production state and can restart; it refuses to run concurrently with the daemon. `--version`, `--config PATH`, `--state-dir PATH` also supported.

Persistent state in `/var/lib/docker-watchdog` (atomic per-project JSON files, exclusive process lock). Counters and rate history survive daemon/host restart. Each down-up attempt persists the up command before down, then retries up even if labelled containers disappear; rate limit applies to retries as well. Failed up may remain pending until cooldown/rate window expires. State uses wall clock; avoid large clock changes. Project state is retained until manually removed, including after project deletion. For planned permanent removal, stop watchdog, remove relevant labels and inspect/remove only the corresponding state JSON if recovery is pending. State files include project name. Do not edit while service is running. Removing package preserves state; purge also preserves state deliberately; manually delete only if desired.

Build: `python3 -m unittest -v && ./build-deb.sh`. GitHub Actions builds/tests on pushes and pull requests. APT publishing: on a trusted build host install `dpkg-dev apt-utils gnupg`, create/select your own signing key, run `./publish-apt.sh /path/to/staging/repo KEY_ID dist/*.deb`. Publish resulting directory over HTTPS atomically (including Packages, Release, InRelease, Release.gpg). Never publish private key. Verify public key fingerprint out of band; install exported key as `/etc/apt/keyrings/docker-watchdog.asc`, mode 0644; add `deb [signed-by=/etc/apt/keyrings/docker-watchdog.asc] https://YOUR-HOST/apt/ ./` to `/etc/apt/sources.list.d/docker-watchdog.list`. Then `apt update && apt install docker-watchdog`. Repo hosting, signing key and publishing credentials are not supplied. CI builds the artifact; deployment is intentionally manual until infrastructure and credentials are chosen.

No Docker runtime integration test in the build environment. Unit tests mock Docker recovery operations. Test on a non-production host before enabling automated recovery.
