# Releasing

Releases publish to PyPI through trusted publishing from `.github/workflows/release.yml`.
No API token is stored anywhere.

## One-time setup (account owner)

1. Create a PyPI account and enable 2FA.
2. On PyPI, add a **pending** trusted publisher (Your projects -> Publishing):
   - PyPI project name: `earwitness`
   - Owner: the GitHub user or organization that hosts the repo
   - Repository name: `earwitness`
   - Workflow name: `release.yml`
   - Environment name: `pypi`
3. In the GitHub repo, create an environment named `pypi` (Settings -> Environments). Optionally
   require a reviewer on it.

A pending publisher does not reserve the name. The name is taken by the first real upload, and
PEP 541 treats empty placeholders as squatting, so the first release must do something useful.
The M1 release (0.0.1) ships the physics core and `earwitness simulate`.

## Cutting a release

1. Bump `version` in `pyproject.toml`, update the README status table, commit.
2. Tag and push: `git tag v0.0.1 && git push origin v0.0.1`.
3. The Release workflow builds, runs the tests, and publishes.
