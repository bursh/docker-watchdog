#!/bin/sh
# Prepare a NEW signed repository directory; publishing it over HTTPS is separate.
set -eu
if [ "$#" -lt 3 ]; then echo 'Usage: publish-rpm.sh NEW_REPO_DIR GPG_KEY_ID package.rpm ...' >&2; exit 2; fi
out=$1; key=$2; shift 2
for tool in createrepo_c rpmsign gpg rpm; do command -v "$tool" >/dev/null || { echo "Missing $tool" >&2; exit 1; }; done
[ ! -e "$out" ] || { echo 'Output directory already exists; use a new staging directory.' >&2; exit 1; }
parent=$(dirname "$out"); mkdir -p "$parent"
work=$(mktemp -d "$parent/.docker-watchdog-rpm.XXXXXX")
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/Packages"
for pkg do
    test -f "$pkg"
    test "$(rpm -qp --qf '%{NAME}' "$pkg")" = docker-watchdog
    name=$(basename "$pkg")
    test ! -e "$work/Packages/$name"
    cp "$pkg" "$work/Packages/$name"
    rpmsign --define "_gpg_name $key" --define "_openpgp_sign_id $key" --addsign "$work/Packages/$name"
done
createrepo_c "$work"
gpg --batch --yes --local-user "$key" --armor --detach-sign "$work/repodata/repomd.xml"
gpg --batch --armor --export "$key" > "$work/RPM-GPG-KEY-docker-watchdog"
test -s "$work/RPM-GPG-KEY-docker-watchdog"
chmod 0755 "$work"
find "$work" -type d -exec chmod 0755 {} +
find "$work" -type f -exec chmod 0644 {} +
mv "$work" "$out"
echo "Signed repository staged in $out; verify signatures and publish atomically over HTTPS."
