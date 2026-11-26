#!/bin/sh
# Build using native rpmbuild; the package is portable noarch (gzip payload).
set -eu
cd "$(dirname "$0")"
command -v rpmbuild >/dev/null || { echo 'Install rpm-build (Fedora/RHEL) or rpm (Debian/Ubuntu).' >&2; exit 1; }
version=$(python3 watchdog.py --version)
out=${1:-dist}
mkdir -p "$out"
out=$(cd "$out" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/rpmbuild/SOURCES" "$work/rpmbuild/SPECS" "$work/source/docker-watchdog-$version/docs"
src="$work/source/docker-watchdog-$version"
for f in watchdog.py watchdog.conf docker-watchdog.service README.md CHANGELOG.md LICENSE; do cp "$f" "$src/"; done
cp docs/docker-watchdog.1 "$src/docs/"
tar -czf "$work/rpmbuild/SOURCES/docker-watchdog-$version.tar.gz" -C "$work/source" "docker-watchdog-$version"
spec=packaging/rpm/docker-watchdog.spec
grep -Eq "^Version:[[:space:]]+$version$" "$spec" || { echo 'RPM version mismatch' >&2; exit 1; }
cp "$spec" "$work/rpmbuild/SPECS/"
# Empty dist suffix keeps one predictable release name across build hosts.
rpmbuild -bb --define "_topdir $work/rpmbuild" --define 'dist %{nil}' \
  --define '_binary_payload w9.gzdio' --define '_build_id_links none' \
  "$work/rpmbuild/SPECS/docker-watchdog.spec"
rpmfile="$work/rpmbuild/RPMS/noarch/docker-watchdog-$version-1.noarch.rpm"
test -f "$rpmfile"
rpm -qp --qf '%{NAME} %{VERSION}-%{RELEASE} %{ARCH}\n' "$rpmfile"
rpm -qpl "$rpmfile"
cp "$rpmfile" "$out/"
