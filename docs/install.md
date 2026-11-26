# Installation guide

## Requirements

Linux with systemd, Python 3.7 or newer, CA certificates, local Docker Engine and Docker Compose V2. Install Docker according to the instructions for your distribution in the [Docker documentation](https://docs.docker.com/engine/install/). Verify `docker info` and `docker compose version` as the service user (root). Do not install a second conflicting Docker distribution.

The packages contain the Python executable, a systemd unit, a commented configuration, man page and documentation. They deliberately do not install Docker Engine or Compose as dependencies. Before production use, test the package and recovery modes in a disposable Linux environment.

## Debian and Ubuntu (including compatible ARM64 systems)

Download the desired `.deb` and `SHA256SUMS` from [GitHub Releases](https://github.com/bursh/docker-watchdog/releases) when available. If checksums list both formats, download both files or check only the matching entry. For example:

```sh
grep 'docker-watchdog_1.3.0-1_all.deb$' SHA256SUMS | sha256sum -c -
sudo apt install ./docker-watchdog_1.3.0-1_all.deb
sudo systemctl status docker-watchdog
```

The package is architecture-independent. Raspberry Pi OS 64-bit is a candidate, not a tested platform. Build from the tagged source using `bash build-deb.sh` with Python and `dpkg-deb` installed, if an asset is not available. The DEB is unsigned; checksums detect corruption, not a compromised publisher.

## Fedora, RHEL-family, Amazon Linux and openSUSE

The new RPM uses `noarch` and targets compatible systemd/Python hosts. It is intended for Fedora, RHEL, Rocky Linux, AlmaLinux and Amazon Linux 2023. openSUSE is a compatibility candidate. Availability of dependencies, install behavior, and recovery must be validated per distribution; these are not all established supported platforms.

```sh
# Fedora / RHEL / Rocky / AlmaLinux / Amazon Linux 2023
# Obtain the RPM and matching SHA256SUMS from a published release.
grep 'docker-watchdog-1.3.0-1.noarch.rpm$' SHA256SUMS | sha256sum -c -
sudo dnf install ./docker-watchdog-1.3.0-1.noarch.rpm
sudo systemctl status docker-watchdog
```

For openSUSE, use `sudo zypper install ./docker-watchdog-1.3.0-1.noarch.rpm` after checking compatibility. Do not disable signature checks globally. Published standalone packages may be unsigned; a signed repository is preferable. The first installation enables the service when systemd is running. If Docker is not ready, start the watchdog after Docker is configured.

Build your own RPM from source on a compatible build host:

```sh
# Fedora or RHEL-family build host
sudo dnf install rpm-build python3
# Alternatively on Debian/Ubuntu: sudo apt install rpm python3
bash build-rpm.sh
rpm -qp --qf '%{NAME} %{VERSION}-%{RELEASE} %{ARCH}\n' dist/*.rpm
rpm -K --nosignature dist/*.rpm
```

The GitHub CI workflow builds the RPM and includes a Fedora container installation/removal smoke test. Check the actual CI result for the release; this does not prove systemd recovery on every RPM distribution.

## Manual installation (including Arch Linux)

Install Python 3, CA certificates, Git and man tooling with your distribution's package manager. Docker Engine and Compose V2 must already work. Use the following only on a compatible systemd host. Verify the release source or use an audited local checkout.

```sh
git clone https://github.com/bursh/docker-watchdog.git
cd docker-watchdog
git checkout v1.3.0
sudo install -m 0755 watchdog.py /usr/local/bin/docker-watchdog
sudo install -d -m 0755 /etc/docker-watchdog
sudo test -e /etc/docker-watchdog/watchdog.conf || sudo install -m 0644 watchdog.conf /etc/docker-watchdog/watchdog.conf
sudo install -d -m 0755 /usr/local/share/man/man1
sudo install -m 0644 docs/docker-watchdog.1 /usr/local/share/man/man1/docker-watchdog.1
sed 's|ExecStart=/usr/bin/docker-watchdog|ExecStart=/usr/local/bin/docker-watchdog|' docker-watchdog.service | sudo tee /etc/systemd/system/docker-watchdog.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now docker-watchdog
```

For manual upgrades, stop the service, replace executable/unit/manual, preserve configuration/state, reload systemd and start again. For manual removal, disable and stop the service, remove the executable, unit and manual, then reload systemd. Keep `/etc/docker-watchdog` and `/var/lib/docker-watchdog` unless deliberately deleting configuration/history. Remove a manual unit override before migrating to a package.

## Upgrade from v1.2.x

Before upgrading a production host, pause affected projects or stop the watchdog and back up configuration/state. Stopping the service prevents its automated recovery; it does not stop Docker or the application containers.

```sh
sudo systemctl stop docker-watchdog
sudo tar -czf docker-watchdog-pre-1.3.0.tar.gz -C / etc/docker-watchdog var/lib/docker-watchdog
# install the new local DEB or RPM using your package manager
sudo systemctl start docker-watchdog
docker-watchdog --version
docker-watchdog --status
```

The DEB conffile may prompt for local changes; the RPM uses `%config(noreplace)` and may create `.rpmnew`. Compare and merge the new options rather than overwriting production settings. The full template is installed at `/usr/share/doc/docker-watchdog/watchdog.conf.example`. Existing project history, rate counters, pauses and pending-up state migrate; new per-container counters start afresh. New state contains a `targets` field unknown to v1.2.x. For rollback, stop the service, downgrade the executable/package, and restore a compatible pre-upgrade state backup; this loses attempts recorded after the backup. Never resume a stale pending recovery blindly.

## Staging signed APT and YUM/DNF repositories

The repository scripts create local repository directories; they do not deploy a website or manage your private keys. Use an administrator-controlled signing key and verify its fingerprint out of band.

APT: install `dpkg-dev apt-utils gnupg` on a trusted build host. Run `./publish-apt.sh /path/to/staging/apt KEY_ID dist/*.deb`. Publish the generated repository over HTTPS. Export the public key into `/etc/apt/keyrings/docker-watchdog.asc`, then configure an APT source:

```text
deb [signed-by=/etc/apt/keyrings/docker-watchdog.asc] https://YOUR-HOST/apt/ ./
```

Run `sudo apt update` and `sudo apt install docker-watchdog`. Do not publish the private key.

YUM/DNF: install `createrepo_c`, `rpm-sign` (or the distribution's RPM signing tool), `rpm` and `gnupg`. Stage a new directory with all desired package versions:

```sh
./publish-rpm.sh /path/to/new-rpm-repo KEY_ID dist/*.rpm
```

The script signs copies of the RPMs, generates `repodata`, signs `repomd.xml`, and exports the public key. Review and verify package and repository signatures before publishing the staged directory atomically over HTTPS. The original build artifacts are not modified. On clients, after verifying the public key fingerprint, configure `/etc/yum.repos.d/docker-watchdog.repo`:

```ini
[docker-watchdog]
name=Docker Watchdog
baseurl=https://YOUR-HOST/rpm/
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=https://YOUR-HOST/rpm/RPM-GPG-KEY-docker-watchdog
```

Run `sudo dnf makecache` and `sudo dnf install docker-watchdog`. No signing key, repository hosting or publication credentials are supplied. Signing and client repository validation must be tested with your RPM/GPG versions.

## First project and verification

Add `watchdog.enabled: "true"` to each monitored Compose service. For HTTP or both checks, publish exactly one distinct TCP port per monitored container. Healthcheck-only targets need no host port. Use the [label configurator](configurator.html) to generate options or consult the [manual](docker-watchdog.1.md). Apply with `docker compose up -d` and run:

```sh
sudo docker-watchdog --list --table
sudo docker-watchdog --status
sudo journalctl -u docker-watchdog --since '24 hours ago'
man docker-watchdog
```

For safe testing, stop the installed daemon before running `sudo docker-watchdog --dry-run`; otherwise the real daemon can still perform recovery. Use `--pause PROJECT` before planned changes and `--resume PROJECT` afterward.

## Unsupported or unvalidated targets

Native Windows and macOS are not supported. Containerized Watchdog, Docker Desktop, rootless Docker, WSL, remote Docker contexts and non-systemd service management have not been validated. Use a dedicated Linux VM with a locally running Docker Engine if appropriate.
