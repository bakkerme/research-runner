# Releasing Research Runner

Research Runner ships through GitHub Releases. Every release contains a universal
Python wheel, a source distribution, a documentation ZIP, and `SHA256SUMS`.
There is no PyPI publication or separate build for each operating system.

## Cut a release

1. Change `[project].version` in `pyproject.toml` and update versioned installation
   examples when appropriate. Use `MAJOR.MINOR.PATCH`, optionally followed by
   `aN`, `bN`, or `rcN`. Merge the reviewed changes and wait for Tests to pass.
2. In GitHub, choose **Releases → Draft a new release**. Create `vVERSION`
   targeting the intended commit, write release notes, and publish. Alternatively,
   push an annotated `vVERSION` tag. The tagged package version must match exactly;
   the workflow does not change versions or manufacture tags.
3. Watch the **Release** workflow. Consumers should wait for its success and all
   four attached assets before installing. A release made through the GitHub UI
   is visible while the workflow runs; an automatically created release stays a
   draft until its assets have been uploaded and downloaded for verification.

The release workflow must be merged before tagging new source. It handles tag
pushes, published releases (including prereleases), and manual recovery. Both
automatic events may occur for one UI release; runs for the same tag serialize.
Complete existing releases reuse and retest their published bundle so duplicate
events do not replace files with builds having different archive timestamps.

## What gates publication

The Release workflow resolves the tag to a commit and checks `pyproject.toml`.
It calls the same Tests workflow used on branches and pull requests, pinned to
that commit. Tests builds the sdist with `python -m build`, which builds the wheel
from the sdist, then runs `tools/package_distribution.py` to check bundled docs,
schemas, examples and the skill and generate the docs ZIP and checksums. Twine
checks distribution metadata. The portability guard checks the committed tree.

The server/fixture suite includes an isolated installed producer CLI against a
disposable HTTP service. Linux, macOS and Windows install the same wheel and run
producer tests outside the checkout without server dependencies. Docker/native
runtime qualification and opt-in browser tests remain separate; a release does
not claim those environments were exercised.

Only the final publication job receives `contents: write`. It downloads the exact
bundle tested by these jobs, checks the four-file inventory and every hash, checks
that the remote tag still points at the tested commit, and uploads missing assets.
It downloads all four release assets to compare their hashes before publishing
any draft. Existing notes and unrelated assets are preserved. Prerelease tags
automatically create prereleases; existing release metadata is retained.

## Retry or recover

If publication fails, choose **Re-run failed jobs** on its Actions run. This
retains the tested build artifact and resumes missing uploads. Matching existing
assets are left alone. Conflicting bytes cause a clear failure; the publisher
never uses `--clobber` or silently replaces a released version.

For an empty release or a tag predating this workflow, use **Actions → Release →
Run workflow**, select `main` for the workflow and enter the existing tag. Or:

```sh
gh workflow run release.yml --ref main -f tag=v0.5.0
```

This runs the current release tooling against the tagged source, builds or
reuses the bundle, tests it, and attaches the assets to the existing release.
An incomplete older release containing conflicting files requires the original
tested artifact; rebuilding can produce different archive bytes even from the
same commit. Do not move or recreate a released tag to work around that error.

The CI artifact is named `research-runner-COMMIT_SHA` and retained for 90 days.
If an older workflow's final publishing job failed but **all** its distribution,
fixture and installed-client jobs passed, a maintainer can download its original
artifact and run the current publisher without rebuilding:

```sh
gh run download RUN_ID --name research-runner-COMMIT_SHA --dir release-recovery
python tools/publish_release.py --repo OWNER/REPOSITORY \
  --tag vVERSION --commit FULL_TESTED_COMMIT_SHA --dist release-recovery
```

Use a new empty recovery directory. Check the source commit and all prerequisite
job conclusions first. The publisher verifies bytes and tag identity; a local
invocation cannot establish that the selected run passed its tests. It requires
an authenticated GitHub CLI with release write access. A complete release can be
retested via the manual workflow as often as needed.

## Reproduce the package locally

Build from a clean checkout into a new output directory so private state and old
packages cannot enter the bundle:

```sh
python -m pip install build twine
python -m build --outdir /tmp/runner-release-dist
python tools/package_distribution.py --dist /tmp/runner-release-dist
python -m twine check --strict /tmp/runner-release-dist/*.whl /tmp/runner-release-dist/*.tar.gz
```

Use a fresh virtual environment to install that wheel and `pytest`, change to a
directory outside the source checkout, and run `tests/test_producer.py` with
`--import-mode=importlib`. Server tests use an editable `.[server,test]` install
and `pytest -q`. Keep generated distributions and recovery downloads out of Git.
