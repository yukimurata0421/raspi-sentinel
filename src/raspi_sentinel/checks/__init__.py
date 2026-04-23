from __future__ import annotations

import socket as _socket
import ssl as _ssl
import subprocess as _subprocess
import time as _time
import urllib.error as _urllib_error
import urllib.request as _urllib_request
from pathlib import Path as _Path

from ..config import TargetConfig
from .command_checks import command_check, run_command_capture, service_active_check
from .file_checks import file_freshness_check
from .models import CheckFailure, CheckResult, ObservationMap, ObservationScalar
from .network_probes import (
    classify_dns_gaierror,
    classify_dns_oserror,
    classify_http_oserror,
    parse_ping_stats,
)
from .runner import apply_records_progress_check
from .semantic_stats import load_stats

# Backward-compatibility aliases used by branch tests.
socket = _socket
ssl = _ssl
subprocess = _subprocess
time = _time
urllib_request = _urllib_request
urllib_error = _urllib_error
Path = _Path
_file_freshness_check = file_freshness_check
_command_check = command_check
_service_active_check = service_active_check
_run_command_capture = run_command_capture
_parse_ping_stats = parse_ping_stats
_classify_dns_gaierror = classify_dns_gaierror
_classify_dns_oserror = classify_dns_oserror
_classify_http_oserror = classify_http_oserror
_load_stats = load_stats


def _stats_checks(
    *,
    target: TargetConfig,
    failures: list[CheckFailure],
    observations: ObservationMap,
    now_wall_ts: float,
) -> None:
    from . import semantic_stats as _semantic_stats

    _semantic_stats.stats_checks(
        target=target,
        failures=failures,
        observations=observations,
        now_wall_ts=now_wall_ts,
    )


def run_checks(target: TargetConfig, now_wall_ts: float | None = None) -> CheckResult:
    from . import runner as _runner
    return _runner.run_checks(target=target, now_wall_ts=now_wall_ts)


__all__ = [
    "CheckFailure",
    "CheckResult",
    "ObservationMap",
    "ObservationScalar",
    "apply_records_progress_check",
    "run_checks",
]
