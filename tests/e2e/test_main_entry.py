from __future__ import annotations

from unittest.mock import patch

from raspi_sentinel.__main__ import main as main_fn


class TestMainEntry:
    @patch("raspi_sentinel.__main__.main", return_value=0)
    def test_main_is_callable(self, mock_main: object) -> None:
        assert callable(main_fn)

    def test_main_bad_config_returns_10(self, tmp_path: object) -> None:
        rc = main_fn(["--config", "/nonexistent/config.toml", "run-once"])
        assert rc == 10
