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

Release tags use the `v` prefix (example: `v0.3.0`), matching the `__version__` string without the prefix.

## Historical note

A lightweight git tag **`v0.2.0`** may exist from an intermediate snapshot. There was **no formal GitHub Release / PyPI publication** tied to that tag. **0.3.0** is the first version where packaging, changelog, and runtime strings are aligned for distribution; use **`v0.3.0`** for the next release tag.

## Release checklist

1. Bump `__version__` in `_version.py` only.
2. Update `CHANGELOG.md` with the new section and date.
3. Run tests (`pytest`).
4. Tag: `git tag -s vX.Y.Z -m "Release vX.Y.Z"` (or unsigned `-a` if you do not use `-s`).
5. Push the tag and publish the GitHub Release / PyPI artifact as you prefer.
