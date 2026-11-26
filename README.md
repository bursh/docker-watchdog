# Docker Watchdog

Centralized HTTP and Docker Healthcheck monitoring with automatic recovery for Docker Compose on Linux.

[![CI](https://github.com/bursh/docker-watchdog/actions/workflows/build.yml/badge.svg)](https://github.com/bursh/docker-watchdog/actions/workflows/build.yml) [![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

[Documentation](https://bursh.github.io/docker-watchdog/) | [Label configurator](https://bursh.github.io/docker-watchdog/configurator.html) | [Manual](docs/docker-watchdog.1.md) | [Releases](https://github.com/bursh/docker-watchdog/releases) | [Changelog](CHANGELOG.md)

## Why Docker Watchdog?

One host service discovers opt-in Compose containers from Docker labels. Monitor several containers in the same project independently, without a manual port registry or third-party Python dependencies. The default is still HTTP monitoring and project restart.

- Four check methods: `http`, `healthcheck`, `both`, `auto`. Healthcheck-only containers need no published port.
- Five recovery modes: `container-restart`, `container-recreate`, `restart`, `force-recreate`, `down-up`; optional escalation.
- Separate failure counters, check schedules and container cooldowns. A shared project lock coordinates recovery. When one target reaches its failure threshold and two targets are confirmed failed (configurable), one whole-project recovery supersedes individual actions.
- Persistent recovery state, shared project rate limiting (`0` = unlimited), maintenance pause/resume, dry-run, CLI, metrics, hooks and HTTPS webhooks.

## Installation

Requires Linux with systemd, Python 3.7+, CA certificates, Docker Engine and Compose V2. Install Docker separately, then verify `docker info` and `docker compose version`. Download the package for your distribution from [Releases](https://github.com/bursh/docker-watchdog/releases) and verify its SHA256 against the matching checksum file. Use an isolated test host first.

```sh
# Debian / Ubuntu (when the v1.3.0 package is available)
sudo apt install ./docker-watchdog_1.3.0-1_all.deb
# Fedora / RHEL-family / Amazon Linux 2023 (when the RPM is available)
sudo dnf install ./docker-watchdog-1.3.0-1.noarch.rpm
```

See the [platform guide](docs/install.md) for source builds, manual installation, upgrades and signed repository staging. RPM builds are new; verify native package behavior on your distribution before production use. No native Windows or macOS support.

## Quick start

Add labels to existing Compose services. For example, an HTTP service publishing one host TCP port and a private backend that already defines Docker Healthcheck:

```yaml
services:
  web:
    image: nginx:alpine
    ports: ["8080:80"]
    labels:
      watchdog.enabled: "true"
      watchdog.check: "http"
      watchdog.path: "/"
      watchdog.mode: "container-restart"
  worker:
    image: alpine:3.22
    command: ["sh", "-c", "rm -f /tmp/watchdog-fail; exec sleep infinity"]
    healthcheck:
      test: ["CMD-SHELL", "test ! -f /tmp/watchdog-fail"]
      interval: 30s
      timeout: 5s
      retries: 3
    labels:
      watchdog.enabled: "true"
      watchdog.check: "healthcheck"
      watchdog.mode: "container-restart"
      watchdog.healthcheck_failures: "1"
```

This is a disposable demonstration, not an application-specific production healthcheck. Use the [configurator](https://bursh.github.io/docker-watchdog/configurator.html) to generate labels with explanations. Then apply and inspect:

```sh
docker compose up -d
sudo docker-watchdog --list --table
sudo docker-watchdog --status
sudo journalctl -u docker-watchdog -f
man docker-watchdog
```

## Multi-container recovery

Each container has its own counters (stable Compose service and replica number) and cadence. The default `project_failure_threshold = 2` promotes to `project_recovery_mode = restart` when at least one target reaches its configured failure threshold and two targets are confirmed failed. Earlier failed observations are reconfirmed before choosing a project operation; they do not add a scheduled failure. Set the promotion threshold to `0` to disable promotion. Container cooldown does not delay other targets; whole-project recovery applies project-wide cooldown. All operations consume a common project rate budget. Global project-policy defaults can be overridden by labels on any monitored service; explicit project-policy labels must agree across services.

Use `--recover PROJECT --mode container-restart --container SERVICE` for an explicit container action. A full container name, full ID or `service:number` is also accepted. The service name must identify exactly one target. `container-recreate` deliberately refuses scaled services, including configured replica counts above one. For precise restart of a replica, use `container-restart` and its full name or ID.

## Configuration and upgrades

Edit `/etc/docker-watchdog/watchdog.conf` and restart the service. The commented default file and installed `watchdog.conf.example` describe all options. HTTP checks run every 60 seconds, with 3 consecutive failures, 120-second cooldown, and 3 attempts per rolling hour by default. Docker-health failures inherit `failures` unless `healthcheck_failures` is nonzero. Existing v1.2.x configuration and labels remain supported. Recovery history, pauses and pending-up records are retained when upgrading; per-target counters are initialized for the new schema. Back up state before upgrading; downgrade to v1.2.x requires restoring compatible state.

See [full usage](docs/usage.md), [manual](docs/docker-watchdog.1.md) and [configuration](watchdog.conf) for effective option precedence, rate limits, status, metrics and maintenance.

## Safety

Docker control is effectively root access. Use only trusted Compose files, labels and hooks. Pause the project before maintenance; an external `down` alone cannot coordinate atomically with active recovery. Use named volumes and fixed published ports for load balancers; recreate modes can replace writable layers and anonymous volumes. A redirect counts as reachable, so use a meaningful health endpoint. A Docker Healthcheck is only as good as its command. See [security policy](SECURITY.md) and the [manual](docs/docker-watchdog.1.md).

## Development

```sh
python3 -m unittest -v
node --test tests/configurator.test.js
./build-deb.sh
./build-rpm.sh          # requires rpmbuild and rpm
# ONLY on a disposable, isolated Docker host:
bash tests/integration.sh
```

GitHub Actions is configured to build both formats and run integration tests. Signed APT/YUM/DNF repositories can be staged using `publish-apt.sh` and `publish-rpm.sh`; hosting and signing keys are not provided. See [contributing](CONTRIBUTING.md) and [publishing](PUBLISHING.md). Apache-2.0 license. Tags v1.0.0 through v1.2.2 were reconstructed from the original source snapshots; the repository's initial commit is preserved.
