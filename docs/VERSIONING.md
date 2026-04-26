# Versioning and releases

## Canonical version string

The release version is defined **once** in:

- `src/raspi_sentinel/_version.py` — `__version__`

`pyproject.toml` does **not** duplicate the number: it uses setuptools **dynamic version** reading that attribute at build time.

`raspi_sentinel.__init__` re-exports `__version__` for `from raspi_sentinel import __version__`.

HTTP `User-Agent` strings in `notify.py` and `time_health.py` use the same value via `from ._version import __version__` so they cannot drift from the package version.

## Semantic versioning (semver)

This project uses **SemVer 2.0** (`MAJOR.MINOR.PATCH`):

- **MAJOR** — incompatible configuration or behavior changes.
- **MINOR** — backward-compatible features and improvements.
- **PATCH** — backward-compatible bug fixes.

## Git tags

Release tags use the `v` prefix (example: `v0.8.0`), matching the `__version__` string without the prefix.

## Historical note

A lightweight git tag **`v0.2.0`** may exist from an intermediate snapshot. There was **no formal GitHub Release / PyPI publication** tied to that tag.

Current state:

- latest stable tag line: **`v0.8.x`**
- open beta line: **`v0.9.x`** (current release target: **`v0.9.0`**)

## Release checklist

1. Bump `__version__` in `_version.py` only.
2. Update `CHANGELOG.md` with the new section and date.
   - promote `[Unreleased]` items into `[X.Y.Z]` and keep a fresh `[Unreleased]` section.
   - reconcile changelog section with `docs/release-notes/vX.Y.Z.md` highlights.
3. Add **`docs/release-notes/vX.Y.Z.md`** with the GitHub Release body (Markdown). The [Release workflow](../.github/workflows/release.yml) uses this path when a tag `vX.Y.Z` is pushed.
   - remove draft markers such as `# Draft:` and `Planned release:` before tagging.
4. Run tests (`pytest`) and Ruff (`ruff check src tests`, `ruff format --check src tests`).
5. Tag: `git tag -a vX.Y.Z -m "Release vX.Y.Z"` (or `git tag -s` if you sign tags).
6. Push the tag (`git push origin vX.Y.Z`). The workflow creates/updates the GitHub Release with the notes file.
7. Publish package index artifacts:
   - `Publish PyPI` workflow runs on GitHub Release publish for production PyPI.
   - `Publish PyPI` can also be manually dispatched for TestPyPI rehearsal.
   - requires Trusted Publisher / OIDC setup for the selected environment.

## PyPI rollout recommendation for beta

For open beta releases, use this order:

1. TestPyPI rehearsal (build + upload + install test)
2. GitHub Release publish
3. Production PyPI publish via Trusted Publisher

Workflow behavior:

- publishing a GitHub Release triggers production PyPI workflow (`pypi.yml`, `release: published`).
- manual `workflow_dispatch` supports explicit TestPyPI/PyPI runs for rehearsal or recovery.

Why:

- reduces install friction for external testers
- verifies packaging metadata before production upload
- avoids long-lived API tokens in repository secrets

## Existing release without notes

If a tag was pushed before `docs/release-notes/vX.Y.Z.md` existed, edit the release on GitHub or run:

`gh release edit vX.Y.Z --notes-file docs/release-notes/vX.Y.Z.md`

## Deprecation warning behavior

- Deprecated `TargetConfig.<field>` shim warnings are emitted once per process per field name.
- In `loop` mode, the same deprecated field access may not re-log every cycle.
- Planned shim removal target remains `v1.0.0`.
