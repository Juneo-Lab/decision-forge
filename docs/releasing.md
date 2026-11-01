# Release process

Releases are cut from `main` by pushing a version tag. The **Release** workflow
(`.github/workflows/release.yml`) does the rest. No maintainer builds or uploads artifacts by hand.

## What the workflow guarantees

| Step | Guarantee |
| --- | --- |
| Version check | The tag equals `v` + the version in `pyproject.toml`, `decision_forge.__version__`, and `CITATION.cff`, and `CHANGELOG.md` has a non-empty section for it |
| Tests | The full test suite passes on the tagged commit |
| Build | The sdist and wheel are built with `uv build` and pass `twine check --strict` |
| Smoke test | The wheel installs into a clean environment, reports the tagged version, and validates the example project |
| Provenance | Each artifact receives a [GitHub build provenance attestation](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations) signed through Sigstore |
| GitHub release | A release named after the tag carries the artifacts and the matching changelog section; PEP 440 pre-releases (`a`, `b`, `rc`) are marked as pre-releases |
| PyPI (opt-in) | Artifacts are uploaded through PyPI trusted publishing without long-lived tokens |

`scripts/check_release.py` runs without a tag in the test suite, so version drift between the package,
citation, and changelog fails ordinary pull request CI as well.

## Cutting a release

1. Open a pull request that:
   - sets the new version in `pyproject.toml`, `src/decision_forge/__init__.py`, and `CITATION.cff`;
   - renames `## Unreleased` in `CHANGELOG.md` to `## X.Y.Z` and adds an empty `## Unreleased` above it;
   - updates version references in `README.md`, `README.zh-CN.md`, and `SECURITY.md`.
2. Run `uv lock` so the lock file records the new project version, then merge after CI passes.
3. Tag the merge commit and push the tag:

   ```bash
   git switch main && git pull --ff-only
   uv run python scripts/check_release.py --tag vX.Y.Z
   git tag -a vX.Y.Z -m "Decision Forge vX.Y.Z"
   git push origin vX.Y.Z
   ```

4. Watch the **Release** workflow. If a step fails, fix the cause on `main`, delete the tag locally and
   remotely, and tag again. Never move a tag after a release has been published.

## Verifying a release

Anyone can confirm that an artifact was built by this repository's release workflow:

```bash
gh release download vX.Y.Z --repo Juneo-Lab/decision-forge --pattern '*.whl'
gh attestation verify decision_forge-X.Y.Z-py3-none-any.whl --repo Juneo-Lab/decision-forge
```

## One-time PyPI setup

PyPI publishing stays disabled until a maintainer opts in:

1. On PyPI, add a [trusted publisher](https://docs.pypi.org/trusted-publishers/) for the
   `decision-forge` project with owner `Juneo-Lab`, repository `decision-forge`, workflow `release.yml`,
   and environment `pypi`.
2. In **Settings → Environments**, create the `pypi` environment and restrict it to `v*` tags.
   Add required reviewers if releases should need a second approval.
3. Set the Actions repository variable `PUBLISH_PYPI` to `true`.

## Versioning

Decision Forge follows [Semantic Versioning](https://semver.org/) with
[PEP 440](https://peps.python.org/pep-0440/) pre-release suffixes. Before 1.0, minor versions may change
export formats, engine fingerprints, and CLI behavior; the changelog states when existing run
directories must be rebuilt. Export formats are versioned independently of the package.
