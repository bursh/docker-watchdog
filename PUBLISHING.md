# Publishing this repository

The five version tags are source snapshots imported in sequence; the initial GitHub commit is preserved exactly. No historical commit timestamps were invented. `main` includes a subsequent documentation and automation commit.

From the extracted directory (including `.git`):

```sh
git remote -v
git fetch origin
git merge-base --is-ancestor origin/main main
git push -u origin main
git push origin --tags
```

If the ancestor check fails, stop and inspect the remote changes; do not force push. The origin URL is https://github.com/bursh/docker-watchdog.git. Use your own Git credentials. Configure your Git identity before future commits.

## Pages

In repository Settings > Pages, select **Deploy from a branch**, then **main** and **/docs**. Save. The static site needs no Jekyll build; `docs/.nojekyll` is included. Expected URL: https://bursh.github.io/docker-watchdog/. Check the Pages deployment status. If desired, preview locally with `python3 -m http.server --directory docs 8000`.

## Releases

The Git tags do not themselves create GitHub Release objects. Use Actions > Publish release > Run workflow on main, enter an existing tag (v1.1.0 or later). The workflow builds the tagged source, validates package version, generates SHA256SUMS and creates a draft release. Review tests, changelog and assets before publishing. For v1.0.0, which has no source package build script, attach the original historical DEB and checksum manually. Alternatively upload the original DEB assets supplied separately, after verifying checksums. Do not overwrite published releases silently.

The existing CI workflow tests and builds on an isolated GitHub runner. Check its first successful run before publishing. Enable private vulnerability reporting, Dependabot alerts and a branch protection/ruleset requiring CI and reviews where appropriate. Set repository description, topics (docker, docker-compose, watchdog, self-healing, systemd, monitoring), and website to the Pages URL. No GitHub settings were modified during local preparation.

## APT

`publish-apt.sh` generates a signed APT repository when supplied with a signing key and DEBs. Do not publish the private key. No APT hosting or signing key is included. See `docs/usage.md` for details.
