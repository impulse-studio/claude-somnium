# Releasing Somnium

Releases are produced by a single manually-triggered GitHub Actions
workflow. You never edit version numbers by hand, you never push tags
by hand, and you never run `twine upload`.

## One-time setup

Before the first release can succeed, two things must be configured
on PyPI. Both take about two minutes.

### 1. Register the project as a PyPI Trusted Publisher

We use [OIDC Trusted Publishing](https://docs.pypi.org/trusted-publishers/),
which means the GitHub Actions workflow authenticates to PyPI using a
short-lived OIDC token instead of a long-lived API token. No secrets
to rotate, no tokens to leak.

1. Go to <https://pypi.org/manage/account/publishing/>.
2. Under **Add a new pending publisher**, fill in:
   - **PyPI Project Name**: `claude-somnium`
   - **Owner**: `impulse-studio`
   - **Repository name**: `claude-somnium`
   - **Workflow name**: `release.yml`
   - **Environment name**: `pypi`
3. Click **Add**.

PyPI lets you register a publisher for a project that doesn't exist
yet (they call it a "pending publisher"). The first successful upload
from the workflow creates the project.

### 2. Create the `pypi` environment on GitHub

The release workflow uses `environment: pypi`, which GitHub checks
against the OIDC claim when talking to PyPI. You just need to create
the environment once:

1. Open the repo on GitHub → **Settings** → **Environments** → **New environment**.
2. Name it `pypi`.
3. (Optional, recommended) Add protection rules:
   - Require **main** branch deployment (so only the main branch can
     trigger it).
   - Require manual approval from one or more reviewers before a
     release publishes.

You don't need to put any secrets in the environment — OIDC does not
use secrets.

### 3. (Optional) Protect main

If you want to be extra safe:

1. **Settings** → **Branches** → add a branch protection rule for `main`.
2. Check "Do not allow bypassing the above settings" and "Require linear
   history".
3. Under "Allow specified actors to bypass required pull requests",
   allow the **github-actions[bot]**.

If you enable branch protection, you'll also need to replace the
`GITHUB_TOKEN` in `release.yml` with a fine-grained PAT stored in
`secrets.RELEASE_TOKEN` that has `contents: write` on this repo.

## Day-to-day workflow

1. Work on `dev`. Push normally, open PRs into `dev`, merge them.
2. `main` stays at the last released state and only moves forward
   during a release.

That's it. There's no release branch, no manual version bumping, no
hand-written `CHANGELOG.md`.

## Cutting a release

1. Make sure `dev` is green on CI and in the state you want to ship.
2. Go to the repo on GitHub → **Actions** → **Release**.
3. Click **Run workflow**.
4. Pick a bump type:
   - **patch** — bug fixes, docs, internal refactors (`0.1.0 → 0.1.1`)
   - **minor** — new features, backwards-compatible (`0.1.0 → 0.2.0`)
   - **major** — breaking changes (`0.1.0 → 1.0.0`)
5. Click **Run workflow**.

The workflow then:

1. Checks out `dev`.
2. Bumps the version in `pyproject.toml` **and** `somnium/__init__.py`.
3. Commits the bump on `dev` (message: `Release vX.Y.Z`).
4. Fast-forwards `main` to `dev`.
5. Creates an annotated tag `vX.Y.Z`.
6. Pushes `dev`, `main`, and the tag atomically.
7. Builds a wheel and an sdist with `python -m build`.
8. Publishes both to PyPI via OIDC.
9. Creates a GitHub Release on the `main` branch with auto-generated
   release notes (pulled from merged PRs and commits since the
   previous tag) and attaches the built artifacts.

Total runtime: roughly two minutes.

## Troubleshooting

**"non-fast-forward" error on main merge.** `main` has diverged from
`dev`, which shouldn't happen under this workflow. Check if somebody
pushed to `main` directly, reset it back to the last release tag, and
rerun.

**"Untrusted publisher" from PyPI.** The trusted publisher registration
doesn't match. Double-check the owner/repo/workflow/environment values
on <https://pypi.org/manage/account/publishing/> — they must match the
strings used in the workflow file exactly.

**"The requested URL returned error: 403" on push.** `GITHUB_TOKEN`
doesn't have permission to push to `main`. Either relax branch
protection for the `github-actions[bot]`, or swap the workflow to use
a PAT stored in `secrets.RELEASE_TOKEN`.

**First release fails because the project doesn't exist on PyPI.**
That's expected if you forgot step 1. Add the pending publisher and
rerun — PyPI creates the project on the first successful upload.

## Rolling back a release

If you publish a broken version:

1. On PyPI, go to the project page → **Manage** → **Releases** →
   **Yank** the broken version. Yanked releases stay installable for
   existing pins but vanish from `pip install claude-somnium` for new
   users.
2. On GitHub, go to the Release page → **Delete** (and optionally
   delete the tag too).
3. Cut a new release with a patch bump that contains the fix.

PyPI does **not** allow deleting a version outright — you can only
yank and move on. Choose your patch bumps carefully.
