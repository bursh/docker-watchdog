# Docker Watchdog

Centralized HTTP monitoring and automatic recovery for Docker Compose projects on Linux.

[![CI](https://github.com/bursh/docker-watchdog/actions/workflows/build.yml/badge.svg)](https://github.com/bursh/docker-watchdog/actions/workflows/build.yml) [![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

[Documentation](https://bursh.github.io/docker-watchdog/) | [Manual source](docs/docker-watchdog.1.md) | [Releases](https://github.com/bursh/docker-watchdog/releases) | [Changelog](CHANGELOG.md)

## Why Docker Watchdog?

One host service discovers opt-in Compose projects using Docker labels, resolves their published HTTP ports, and recovers the **whole project** after consecutive failed checks. No per-project port registry or Python dependencies beyond the standard library.

- Three recovery modes: `restart`, `force-recreate`, `down-up`; optional escalation.
- Per-project health checks, cooldown, configurable restart limits (`0` = unlimited), persistent state and pending recovery.
- Maintenance pause/resume, dry-run, human-readable CLI, JSON output, systemd logs, optional local Prometheus metrics and generic webhooks.

## Quick start (Debian / Ubuntu)

Requires Linux with systemd, Python 3.7+, running Docker Engine and Compose V2. Check `docker compose version`. Use a dedicated test host first. Download the matching DEB and checksum from Releases (once published), or build from source:

```sh
python3 -m unittest -v
./build-deb.sh
sudo apt install ./dist/docker-watchdog_1.2.2-1_all.deb
```

Add this label to the existing HTTP service publishing one TCP port:

```yaml
labels:
  watchdog.enabled: "true"
  watchdog.path: "/"
  watchdog.mode: "restart"
```

Apply with `docker compose up -d` and run:

```sh
sudo docker-watchdog --list --table
sudo docker-watchdog --status
sudo journalctl -u docker-watchdog -f
man docker-watchdog
```

## Configuration

Edit `/etc/docker-watchdog/watchdog.conf`, then restart the service. Default checks run every 60 seconds, with 3 consecutive failures, 120-second cooldown and 3 attempts per rolling hour. Labels override supported global options. Unlimited attempts require explicit `max_restarts = 0`. See [configuration](watchdog.conf), [full usage](docs/usage.md) and [manual](docs/docker-watchdog.1.md).

## Platform support

Debian/Ubuntu DEB is the primary distribution. Other systemd Linux distributions can use the manual installation in [platform installation guide](docs/install.md); those paths have not been integration-tested. No native Windows/macOS support. Docker Desktop/WSL configurations are not supported as tested targets.

## Safety

Docker control is effectively root access. Use only trusted Compose files, labels and hooks. Pause a project before maintenance; an external `down` alone cannot coordinate atomically with active recovery. Use named volumes and fixed published ports for load balancers; recreate modes can replace writable layers and anonymous volumes. A redirect counts as reachable, so use a meaningful health endpoint. See [security policy](SECURITY.md) and [limitations](docs/usage.md).

## Development

```sh
python3 -m unittest -v
bash build-deb.sh
# Only on a disposable, isolated Docker host:
bash tests/integration.sh
```

See [contributing](CONTRIBUTING.md). Apache-2.0 license. The release tags v1.0.0 through v1.2.2 are reconstructed from original source snapshots; they are not fabricated historical commits. The original repository initialization is preserved.
