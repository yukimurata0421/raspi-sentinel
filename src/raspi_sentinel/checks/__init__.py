from __future__ import annotations

from ..config import TargetConfig
from .models import CheckFailure, CheckResult, ObservationMap, ObservationScalar
from .runner import apply_records_progress_check


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
