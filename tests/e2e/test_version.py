from __future__ import annotations

import raspi_sentinel
from raspi_sentinel._version import __version__ as version_module


def test_version_matches_across_import_paths() -> None:
    assert raspi_sentinel.__version__ == version_module
