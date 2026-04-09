from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import logging
from pathlib import Path
import subprocess
import time
from typing import Any

from .config import TargetConfig

LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class CheckFailure:
    check: str
    message: str


@dataclass(slots=True)
class CheckResult:
    target: str
    healthy: bool
    failures: list[CheckFailure]
    observations: dict[str, Any] = field(default_factory=dict)


def _file_freshness_check(path: Path, max_age_sec: int, check_name: str) -> CheckFailure | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return CheckFailure(check_name, f"file missing: {path}")
    except OSError as exc:
        return CheckFailure(check_name, f"cannot stat file {path}: {exc}")

    age = time.time() - stat.st_mtime
    if age > max_age_sec:
        return CheckFailure(
            check_name,
            f"file stale: {path} age={age:.1f}s max={max_age_sec}s",
        )
    return None


def _command_check(command: str, timeout_sec: int, check_name: str = "command") -> CheckFailure | None:
    try:
        result = subprocess.run(
            command,
            shell=True,
            check=False,
            timeout=timeout_sec,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        return CheckFailure(check_name, f"command timeout after {timeout_sec}s: {command}")
    except OSError as exc:
        return CheckFailure(check_name, f"command failed to start: {exc}")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        snippet = stderr or stdout or "no output"
        return CheckFailure(
            check_name,
            f"command exit code {result.returncode}: {command}; output={snippet[:300]}",
        )

    return None


def _service_active_check(service: str) -> CheckFailure | None:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", service],
            check=False,
            timeout=10,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        return CheckFailure("service_active", f"systemctl is-active timeout for service {service}")
    except OSError as exc:
        return CheckFailure("service_active", f"cannot run systemctl for {service}: {exc}")

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "inactive"
        return CheckFailure("service_active", f"service not active: {service} ({detail})")

    return None


def _parse_ts(raw: Any, field_name: str) -> tuple[float | None, str | None]:
    if not isinstance(raw, str) or not raw.strip():
        return None, f"{field_name} must be a non-empty RFC3339 timestamp string"

    normalized = raw.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        ts = datetime.fromisoformat(normalized)
    except ValueError:
        return None, f"{field_name} has invalid timestamp format: {raw}"

    if ts.tzinfo is None:
        return None, f"{field_name} must include timezone offset"
    return ts.timestamp(), None


def _age_check_from_stats(
    stats: dict[str, Any],
    key: str,
    max_age_sec: int,
    now_ts: float,
    check_name: str,
) -> CheckFailure | None:
    ts_raw = stats.get(key)
    ts, err = _parse_ts(ts_raw, key)
    if err:
        return CheckFailure(check_name, err)

    age = now_ts - ts
    if age > max_age_sec:
        return CheckFailure(check_name, f"{key} stale: age={age:.1f}s max={max_age_sec}s")
    return None


def _load_stats(path: Path) -> tuple[dict[str, Any] | None, CheckFailure | None]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, CheckFailure("semantic_stats_file", f"stats file missing: {path}")
    except OSError as exc:
        return None, CheckFailure("semantic_stats_file", f"cannot read stats file {path}: {exc}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, CheckFailure("semantic_stats_file", f"invalid JSON in stats file {path}: {exc}")

    if not isinstance(data, dict):
        return None, CheckFailure("semantic_stats_file", f"stats file root must be JSON object: {path}")
    return data, None


def _stats_checks(
    target: TargetConfig,
    failures: list[CheckFailure],
    observations: dict[str, Any],
) -> None:
    if target.stats_file is None:
        return

    stats, failure = _load_stats(target.stats_file)
    if failure is not None:
        failures.append(failure)
        return
    if stats is None:
        return

    now_ts = time.time()
    updated_ts_raw = stats.get("updated_at")
    updated_ts, updated_ts_err = _parse_ts(updated_ts_raw, "updated_at")
    if updated_ts is not None:
        observations["stats_age_sec"] = now_ts - updated_ts
    if target.stats_updated_max_age_sec is not None:
        if updated_ts_err is not None:
            failures.append(CheckFailure("semantic_updated_at", updated_ts_err))
        else:
            age = now_ts - updated_ts
            if age > target.stats_updated_max_age_sec:
                failures.append(
                    CheckFailure(
                        "semantic_updated_at",
                        (
                            "updated_at stale: "
                            f"age={age:.1f}s max={target.stats_updated_max_age_sec}s"
                        ),
                    )
                )

    if target.stats_last_input_max_age_sec is not None:
        failure = _age_check_from_stats(
            stats=stats,
            key="last_input_ts",
            max_age_sec=target.stats_last_input_max_age_sec,
            now_ts=now_ts,
            check_name="semantic_last_input_ts",
        )
        if failure:
            failures.append(failure)

    if target.stats_last_success_max_age_sec is not None:
        failure = _age_check_from_stats(
            stats=stats,
            key="last_success_ts",
            max_age_sec=target.stats_last_success_max_age_sec,
            now_ts=now_ts,
            check_name="semantic_last_success_ts",
        )
        if failure:
            failures.append(failure)

    status_raw = stats.get("status")
    if status_raw is not None:
        if not isinstance(status_raw, str):
            failures.append(CheckFailure("semantic_status", "status must be string when set"))
        else:
            observations["stats_status"] = status_raw
            if status_raw not in ("ok", "healthy"):
                failures.append(CheckFailure("semantic_status", f"status is not healthy: {status_raw}"))

    records_raw = stats.get("records_processed_total")
    if records_raw is not None:
        try:
            records = int(records_raw)
        except (TypeError, ValueError):
            failures.append(
                CheckFailure(
                    "semantic_records_total",
                    "records_processed_total must be integer when set",
                )
            )
        else:
            observations["records_processed_total"] = records

    dns_ok = stats.get("dns_ok")
    if dns_ok is not None:
        if not isinstance(dns_ok, bool):
            failures.append(CheckFailure("dependency_dns", "dns_ok must be boolean when set"))
        else:
            observations["dns_ok"] = dns_ok
            if not dns_ok:
                failures.append(CheckFailure("dependency_dns", "dns_ok=false in stats file"))

    gateway_ok = stats.get("gateway_ok")
    if gateway_ok is not None:
        if not isinstance(gateway_ok, bool):
            failures.append(CheckFailure("dependency_gateway", "gateway_ok must be boolean when set"))
        else:
            observations["gateway_ok"] = gateway_ok
            if not gateway_ok:
                failures.append(CheckFailure("dependency_gateway", "gateway_ok=false in stats file"))


def run_checks(target: TargetConfig) -> CheckResult:
    failures: list[CheckFailure] = []
    observations: dict[str, Any] = {}

    if target.heartbeat_file is not None and target.heartbeat_max_age_sec is not None:
        failure = _file_freshness_check(
            target.heartbeat_file,
            target.heartbeat_max_age_sec,
            "heartbeat_file",
        )
        if failure:
            failures.append(failure)

    if target.output_file is not None and target.output_max_age_sec is not None:
        failure = _file_freshness_check(target.output_file, target.output_max_age_sec, "output_file")
        if failure:
            failures.append(failure)

    if target.command:
        timeout_sec = target.command_timeout_sec or 10
        failure = _command_check(target.command, timeout_sec, check_name="command")
        if failure:
            failures.append(failure)

    if target.dns_check_command:
        timeout_sec = target.dependency_check_timeout_sec or 10
        failure = _command_check(target.dns_check_command, timeout_sec, check_name="dependency_dns")
        observations["dns_ok"] = failure is None
        if failure:
            failures.append(failure)

    if target.gateway_check_command:
        timeout_sec = target.dependency_check_timeout_sec or 10
        failure = _command_check(
            target.gateway_check_command,
            timeout_sec,
            check_name="dependency_gateway",
        )
        observations["gateway_ok"] = failure is None
        if failure:
            failures.append(failure)

    _stats_checks(target=target, failures=failures, observations=observations)

    if target.service_active:
        for service in target.services:
            failure = _service_active_check(service)
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
