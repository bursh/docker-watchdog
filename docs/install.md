# Installation guide

## Requirements

A Linux host with systemd, Python 3.7 or newer, CA certificates, local Docker Engine and Docker Compose V2. Install Docker using the instructions for your distribution from [Docker documentation](https://docs.docker.com/engine/install/). Verify `docker info` and `docker compose version` as the service user (root). Do not install a second conflicting Docker distribution.

## Debian and Ubuntu (including compatible ARM64 systems)

Download the `.deb` and `SHA256SUMS` for the desired version from [GitHub Releases](https://github.com/bursh/docker-watchdog/releases), when available. In the download directory verify the checksum, then install:

```sh
sha256sum -c SHA256SUMS
sudo apt install ./docker-watchdog_1.2.2-1_all.deb
sudo systemctl status docker-watchdog
```

The package is architecture-independent; Docker and Python must support the host architecture. Raspberry Pi OS 64-bit is a candidate, not a tested platform. If no release asset is available, clone the repository, check out `v1.2.2` and run `bash build-deb.sh` with Python and `dpkg-deb` installed. The DEB is unsigned; checksums detect corruption, not a compromised publisher. A signed APT repository is optional and is not currently deployed.

## Fedora, RHEL-family, Amazon Linux 2023 and Arch Linux

Manual installation is intended for compatible systemd hosts; these distributions have not been integration-tested. Install Python 3, CA certificates, Git and man-page tooling from your distribution. For Fedora/RHEL-family/Amazon Linux use `dnf` (package names can vary); for Arch use `pacman`. Docker Engine and Compose V2 must already work. No RPM or Arch package is provided.

```sh
git clone https://github.com/bursh/docker-watchdog.git
cd docker-watchdog
git checkout v1.2.2
sudo install -m 0755 watchdog.py /usr/local/bin/docker-watchdog
sudo install -d -m 0755 /etc/docker-watchdog
# Keep an existing local configuration intact.
sudo test -e /etc/docker-watchdog/watchdog.conf || sudo install -m 0644 watchdog.conf /etc/docker-watchdog/watchdog.conf
sudo install -d -m 0755 /usr/local/share/man/man1
sudo install -m 0644 docs/docker-watchdog.1 /usr/local/share/man/man1/docker-watchdog.1
sed 's|ExecStart=/usr/bin/docker-watchdog|ExecStart=/usr/local/bin/docker-watchdog|' docker-watchdog.service | sudo tee /etc/systemd/system/docker-watchdog.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now docker-watchdog
```

For manual upgrades, stop the service first, replace the executable and unit, preserve configuration/state, reload systemd and start again. For manual removal disable and stop the service, remove the installed executable, unit and manual, then reload systemd. Keep `/etc/docker-watchdog` and `/var/lib/docker-watchdog` unless you intentionally want to delete configuration and recovery history. Remove a manual override before migrating to a DEB.

## First project and verification

Add `watchdog.enabled: "true"` to exactly one existing published HTTP service. Use `watchdog.path` for a meaningful health endpoint and fixed ports for ALB. Apply the Compose file and run `sudo docker-watchdog --list --table`, `sudo docker-watchdog --status`, and `sudo journalctl -u docker-watchdog -f`.

For safe testing, stop the installed daemon before running `sudo docker-watchdog --dry-run`; otherwise the real daemon can still recover containers. Use `--pause PROJECT` before planned changes and `--resume PROJECT` afterward.

## Unsupported targets

Native Windows and macOS are not supported. Containerized Watchdog, Docker Desktop, rootless Docker, WSL, remote Docker contexts and non-systemd service management have not been validated. Use a dedicated Linux VM with a locally running Docker Engine if appropriate.

See the [manual](docker-watchdog.1.md) for full options, security, recovery behavior and troubleshooting.
