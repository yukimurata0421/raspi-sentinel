from __future__ import annotations

import logging
import time

from ..config import TargetConfig
from ..state_helpers import safe_optional_int
from ..state_models import TargetState
from .command_checks import DEFAULT_TIMEOUT_SEC, command_check, service_active_check
from .file_checks import file_freshness_check
from .models import CheckFailure, CheckResult, ObservationMap
from .network_probes import probe_network_uplink
from .semantic_stats import external_status_checks, stats_checks

LOG = logging.getLogger(__name__)


def apply_records_progress_check(
    target: TargetConfig,
    target_state: TargetState,
    result: CheckResult,
) -> None:
    """Detect stalled ``records_processed_total`` in semantic stats (same cycle as other checks)."""
    stall_cycles_threshold = target.stats.stats_records_stall_cycles
    if stall_cycles_threshold is None:
        return

    current_records = safe_optional_int(result.observations.get("records_processed_total"))
    if current_records is None:
        return

    previous_records = target_state.last_records_processed_total
    stalled_cycles = target_state.records_stalled_cycles

    if previous_records is None or current_records < previous_records:
        stalled_cycles = 0
    elif current_records == previous_records:
        stalled_cycles += 1
        if stalled_cycles >= stall_cycles_threshold:
            result.failures.append(
                CheckFailure(
                    "semantic_records_stalled",
                    (
                        "records_processed_total is not increasing: "
                        f"value={current_records} stalled_cycles={stalled_cycles} "
                        f"threshold={stall_cycles_threshold}"
                    ),
                )
            )
    else:
        stalled_cycles = 0

    target_state.last_records_processed_total = current_records
    target_state.records_stalled_cycles = stalled_cycles
    result.healthy = not result.failures


def _effective_timeout(raw_timeout: int | None) -> int:
    return raw_timeout if raw_timeout is not None else DEFAULT_TIMEOUT_SEC


def run_checks(target: TargetConfig, now_wall_ts: float | None = None) -> CheckResult:
    failures: list[CheckFailure] = []
    observations: ObservationMap = {}
    wall = time.time() if now_wall_ts is None else now_wall_ts

    def _run_dependency_check(
        command: str | None,
        use_shell: bool,
        observation_key: str,
        check_name: str,
    ) -> None:
        if not command:
            return
        timeout_sec = _effective_timeout(target.deps.dependency_check_timeout_sec)
        failure = command_check(
            command,
            timeout_sec,
            check_name=check_name,
            use_shell=use_shell,
        )
        observations[observation_key] = failure is None
        if failure:
            failures.append(failure)

    def _append_dependency_failure(check_name: str, message: str) -> None:
        if any(f.check == check_name for f in failures):
            return
        failures.append(CheckFailure(check_name, message))

    if target.heartbeat_file is not None and target.heartbeat_max_age_sec is not None:
        failure = file_freshness_check(
            target.heartbeat_file,
            target.heartbeat_max_age_sec,
            "heartbeat_file",
            now_wall_ts=wall,
        )
        if failure:
            failures.append(failure)

    if target.output_file is not None and target.output_max_age_sec is not None:
        failure = file_freshness_check(
            target.output_file,
            target.output_max_age_sec,
            "output_file",
            now_wall_ts=wall,
        )
        if failure:
            failures.append(failure)

    if target.command:
        timeout_sec = _effective_timeout(target.command_timeout_sec)
        failure = command_check(
            target.command,
            timeout_sec,
            check_name="command",
            use_shell=target.command_use_shell,
        )
        if failure:
            failures.append(failure)

    _run_dependency_check(
        command=target.deps.link_check_command,
        use_shell=target.deps.link_check_use_shell,
        observation_key="link_ok",
        check_name="dependency_link",
    )
    _run_dependency_check(
        command=target.deps.default_route_check_command,
        use_shell=target.deps.default_route_check_use_shell,
        observation_key="default_route_ok",
        check_name="dependency_default_route",
    )
    _run_dependency_check(
        command=target.deps.gateway_check_command,
        use_shell=target.deps.gateway_check_use_shell,
        observation_key="gateway_ok",
        check_name="dependency_gateway",
    )
    _run_dependency_check(
        command=target.deps.internet_ip_check_command,
        use_shell=target.deps.internet_ip_check_use_shell,
        observation_key="internet_ip_ok",
        check_name="dependency_internet_ip",
    )
    _run_dependency_check(
        command=target.deps.dns_server_check_command,
        use_shell=target.deps.dns_server_check_use_shell,
        observation_key="dns_server_reachable",
        check_name="dependency_dns_server",
    )
    _run_dependency_check(
        command=target.deps.dns_check_command,
        use_shell=target.deps.dns_check_use_shell,
        observation_key="dns_ok",
        check_name="dependency_dns",
    )
    _run_dependency_check(
        command=target.deps.wan_vs_target_check_command,
        use_shell=target.deps.wan_vs_target_check_use_shell,
        observation_key="wan_vs_target_ok",
        check_name="dependency_wan_target",
    )

    stats_checks(target=target, failures=failures, observations=observations, now_wall_ts=wall)
    external_status_checks(
        target=target,
        failures=failures,
        observations=observations,
        now_wall_ts=wall,
    )
    probe_network_uplink(target=target, observations=observations)

    dependency_observation_checks = (
        ("link_ok", "dependency_link"),
        ("default_route_ok", "dependency_default_route"),
        ("gateway_ok", "dependency_gateway"),
        ("internet_ip_ok", "dependency_internet_ip"),
        ("dns_ok", "dependency_dns"),
        ("http_probe_ok", "dependency_http_probe"),
    )
    for observation_key, check_name in dependency_observation_checks:
        # Network probes can populate *_ok=False even when explicit dependency
        # commands are not configured; convert those observations into policy-
        # visible dependency failures without duplicating existing check names.
        if observations.get(observation_key) is False:
            _append_dependency_failure(check_name, f"{observation_key}=false")

    if target.service_active:
        service_timeout = _effective_timeout(target.command_timeout_sec)
        for service in target.services:
            failure = service_active_check(service, timeout_sec=service_timeout)
            if failure:
                failures.append(failure)

    healthy = not failures
    if healthy:
        LOG.debug("target '%s' passed all health checks", target.name)
    else:
        LOG.warning(
            "target '%s' failed checks: %s",
            target.name,
            "; ".join(f"{f.check}: {f.message}" for f in failures),
        )

    return CheckResult(
        target=target.name,
        healthy=healthy,
        failures=failures,
        observations=observations,
    )
