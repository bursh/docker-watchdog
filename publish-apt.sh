#!/bin/sh
# Usage: publish-apt.sh REPO_DIR GPG_KEY DEB...
set -eu
repo=$1; key=$2; shift 2
mkdir -p "$repo"
cp "$@" "$repo/"
cd "$repo"
dpkg-scanpackages --multiversion . /dev/null > Packages.tmp
mv Packages.tmp Packages
gzip -n -c Packages > Packages.gz
apt-ftparchive release . > Release.tmp
mv Release.tmp Release
gpg --batch --yes --local-user "$key" --clearsign -o InRelease Release
gpg --batch --yes --local-user "$key" --armor --detach-sign -o Release.gpg Release
gpg --armor --export "$key" > docker-watchdog.asc
