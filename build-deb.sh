#!/bin/sh
set -eu
cd "$(dirname "$0")"
out=${1:-dist}
mkdir -p "$out"
root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
install -d "$root/DEBIAN" "$root/usr/bin" "$root/usr/lib/docker-watchdog" "$root/etc/docker-watchdog" "$root/lib/systemd/system" "$root/usr/share/doc/docker-watchdog"
install -m 755 watchdog.py "$root/usr/bin/docker-watchdog"
install -m 644 watchdog.conf "$root/etc/docker-watchdog/watchdog.conf"
install -m 644 docker-watchdog.service "$root/lib/systemd/system/"
install -m 644 README.md CHANGELOG.md "$root/usr/share/doc/docker-watchdog/"
install -m 644 LICENSE "$root/usr/share/doc/docker-watchdog/copyright"
install -m 644 watchdog.conf "$root/usr/share/doc/docker-watchdog/watchdog.conf.example"
install -d "$root/usr/share/man/man1"
gzip -n -9 -c docs/docker-watchdog.1 > "$root/usr/share/man/man1/docker-watchdog.1.gz"
chmod 644 "$root/usr/share/man/man1/docker-watchdog.1.gz"
for f in postinst prerm postrm; do install -m 755 "packaging/$f" "$root/DEBIAN/$f"; done
cat > "$root/DEBIAN/control" <<'CONTROL'
Package: docker-watchdog
Version: 1.3.0-1
Section: admin
Priority: optional
Architecture: all
Depends: python3 (>= 3.7), systemd, init-system-helpers, ca-certificates
Maintainer: Docker Watchdog Package Maintainer <maintainer@example.invalid>
Homepage: https://github.com/bursh/docker-watchdog
Description: Central Docker Compose watchdog with HTTP and healthcheck recovery
 Requires an existing Docker Engine and Docker Compose V2 installation.
CONTROL
printf '%s\n' /etc/docker-watchdog/watchdog.conf > "$root/DEBIAN/conffiles"
dpkg-deb --root-owner-group --build "$root" "$out/docker-watchdog_1.3.0-1_all.deb"
