from __future__ import annotations

import warnings
from typing import Any

from conftest import make_target

from raspi_sentinel import config_models


def _read_target_attr_from_internal_module(target: Any) -> Any:
    namespace: dict[str, Any] = {}
    exec(
        "def _inner(t):\n    return t.dns_check_command\n",
        {"__name__": "raspi_sentinel.internal_test"},
        namespace,
    )
    return namespace["_inner"](target)


def test_target_flat_attr_warning_once_and_reset() -> None:
    config_models._reset_deprecated_attr_warnings_for_tests()
    target = make_target(dns_check_command="dig +short example.com")

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", DeprecationWarning)
        assert target.dns_check_command == "dig +short example.com"
        assert target.dns_check_command == "dig +short example.com"
    assert len(captured) == 1
    assert "planned removal in v1.0.0" in str(captured[0].message)

    config_models._reset_deprecated_attr_warnings_for_tests()
    with warnings.catch_warnings(record=True) as captured_after_reset:
        warnings.simplefilter("always", DeprecationWarning)
        assert target.dns_check_command == "dig +short example.com"
    assert len(captured_after_reset) == 1


def test_target_flat_attr_warning_is_suppressed_for_internal_callers() -> None:
    config_models._reset_deprecated_attr_warnings_for_tests()
    target = make_target(dns_check_command="dig +short example.com")

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", DeprecationWarning)
        assert _read_target_attr_from_internal_module(target) == "dig +short example.com"
    assert captured == []


def test_target_flat_attr_second_access_skips_frame_lookup(monkeypatch: Any) -> None:
    config_models._reset_deprecated_attr_warnings_for_tests()
    target = make_target(dns_check_command="dig +short example.com")

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always", DeprecationWarning)
        assert target.dns_check_command == "dig +short example.com"

    def _unexpected_currentframe() -> Any:
        raise AssertionError("inspect.currentframe must not be called for already warned attrs")

    monkeypatch.setattr(config_models.inspect, "currentframe", _unexpected_currentframe)
    assert target.dns_check_command == "dig +short example.com"
