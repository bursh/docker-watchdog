# Publishing Docker Watchdog

The initial GitHub commit is preserved exactly, followed by five original release snapshot imports (v1.0.0 through v1.2.2). The new v1.3.0 changes are normal development commits. No historical commit timestamps were invented. Do not move or delete published tags.

## Push the development branch and open a PR

The main branch is protected by a ruleset and requires the `build` CI check. From the extracted directory (including `.git`), use your own Git credentials:

```sh
git remote -v
git fetch origin
git log --oneline --decorate --graph --all
git merge-base --is-ancestor origin/main HEAD \
  && git push -u origin HEAD:refs/heads/release/v1.3.0
```

If the ancestor check fails, inspect changes and integrate the remote branch without a force push before pushing. Open a pull request from `release/v1.3.0` to `main`. Review all changes, ensure CI (including RPM build/install smoke test and Docker integration tests) passes, then use squash or rebase merge according to the branch ruleset. Verify the merged revision has the intended version and files.

## Tag and release

After merging, tag the verified merged main commit; the new immutable tag is intentionally not included in the prepared branch archive.

```sh
git fetch origin
git log -1 --oneline origin/main
git show origin/main:watchdog.py | grep '^VERSION = '
git tag -a v1.3.0 origin/main -m 'Docker Watchdog v1.3.0'
git push origin v1.3.0
```

Do not overwrite an existing version tag. Use Actions > Publish release > Run workflow on main and select the existing tag `v1.3.0`. The workflow tests the tagged source, builds DEB and RPM, validates metadata, generates SHA256SUMS, and creates a draft GitHub Release. Review the generated assets, actual CI results and changelog before publishing. Historical tags may only support DEB; v1.0.0 has no original source build script. Do not overwrite published releases silently. The current release artifact is unsigned until separately signed or distributed through a trusted signed repository.

## GitHub Pages

Pages uses main `/docs` (Deploy from a branch), with `.nojekyll`. After merge, check the Pages deployment and visit https://bursh.github.io/docker-watchdog/ and https://bursh.github.io/docker-watchdog/configurator.html. The configurator is a static HTML/JS page with no backend, telemetry or external libraries. Preview locally:

```sh
python3 -m http.server --directory docs 8000
```

## Signed repositories

`publish-apt.sh` stages a signed APT repository; `publish-rpm.sh` stages signed RPM packages and metadata for a YUM/DNF repository. Both require your own signing key and hosting. See [installation](docs/install.md). Do not publish private keys or deployment credentials.
