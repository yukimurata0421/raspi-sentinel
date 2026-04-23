from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Mapping

from ..config import TargetConfig
from .models import CheckFailure, ObservationMap


def parse_ts(raw: object, field_name: str) -> tuple[float | None, str | None]:
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


def age_check_from_stats(
    stats: Mapping[str, object],
    key: str,
    max_age_sec: int,
    now_ts: float,
    check_name: str,
) -> CheckFailure | None:
    ts_raw = stats.get(key)
    ts, err = parse_ts(ts_raw, key)
    if err:
        return CheckFailure(check_name, err)
    # parse_ts guarantees non-None ts when err is None; keep this guard for type narrowing.
    if ts is None:
        return CheckFailure(check_name, f"{key} missing timestamp")

    age = now_ts - ts
    if age > max_age_sec:
        return CheckFailure(check_name, f"{key} stale: age={age:.1f}s max={max_age_sec}s")
    return None


def load_stats(path: Path) -> tuple[dict[str, object] | None, CheckFailure | None]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, CheckFailure("semantic_stats_file", f"stats file missing: {path}")
    except OSError as exc:
        return None, CheckFailure("semantic_stats_file", f"cannot read stats file {path}: {exc}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, CheckFailure(
            "semantic_stats_file", f"invalid JSON in stats file {path}: {exc}"
        )

    if not isinstance(data, dict):
        return None, CheckFailure(
            "semantic_stats_file", f"stats file root must be JSON object: {path}"
        )
    return data, None


def stats_checks(
    target: TargetConfig,
    failures: list[CheckFailure],
    observations: ObservationMap,
    now_wall_ts: float,
) -> None:
    stats_cfg = target.stats
    if stats_cfg.stats_file is None:
        return

    stats, failure = load_stats(stats_cfg.stats_file)
    if failure is not None:
        failures.append(failure)
        return
    if stats is None:
        return

    now_ts = now_wall_ts
    updated_ts_raw = stats.get("updated_at")
    updated_ts, updated_ts_err = parse_ts(updated_ts_raw, "updated_at")
    if updated_ts is not None:
        observations["stats_age_sec"] = now_ts - updated_ts
    if stats_cfg.stats_updated_max_age_sec is not None:
        if updated_ts_err is not None:
            failures.append(CheckFailure("semantic_updated_at", updated_ts_err))
        else:
            if updated_ts is None:
                failures.append(CheckFailure("semantic_updated_at", "updated_at missing timestamp"))
                return
            age = now_ts - updated_ts
            if age > stats_cfg.stats_updated_max_age_sec:
                failures.append(
                    CheckFailure(
                        "semantic_updated_at",
                        (
                            "updated_at stale: "
                            f"age={age:.1f}s max={stats_cfg.stats_updated_max_age_sec}s"
                        ),
                    )
                )

    if stats_cfg.stats_last_input_max_age_sec is not None:
        failure = age_check_from_stats(
            stats=stats,
            key="last_input_ts",
            max_age_sec=stats_cfg.stats_last_input_max_age_sec,
            now_ts=now_ts,
            check_name="semantic_last_input_ts",
        )
        if failure:
            failures.append(failure)

    if stats_cfg.stats_last_success_max_age_sec is not None:
        failure = age_check_from_stats(
            stats=stats,
            key="last_success_ts",
            max_age_sec=stats_cfg.stats_last_success_max_age_sec,
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
                failures.append(
                    CheckFailure("semantic_status", f"status is not healthy: {status_raw}")
                )

    records_raw = stats.get("records_processed_total")
    if records_raw is not None:
        if isinstance(records_raw, bool) or not isinstance(
            records_raw, (int, float, str, bytes, bytearray)
        ):
            failures.append(
                CheckFailure(
                    "semantic_records_total",
                    "records_processed_total must be integer when set",
                )
            )
        else:
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

    dependency_bool_fields = (
        ("link_ok", "dependency_link"),
        ("default_route_ok", "dependency_default_route"),
        ("gateway_ok", "dependency_gateway"),
        ("internet_ip_ok", "dependency_internet_ip"),
        ("dns_server_reachable", "dependency_dns_server"),
        ("dns_ok", "dependency_dns"),
        ("wan_vs_target_ok", "dependency_wan_target"),
    )
    for field_name, check_name in dependency_bool_fields:
        raw = stats.get(field_name)
        if raw is None:
            continue
        if not isinstance(raw, bool):
            failures.append(CheckFailure(check_name, f"{field_name} must be boolean when set"))
            continue
        observations[field_name] = raw
        if not raw:
            failures.append(CheckFailure(check_name, f"{field_name}=false in stats file"))

    dns_latency_raw = stats.get("dns_latency_ms")
    if dns_latency_raw is not None:
        if isinstance(dns_latency_raw, bool) or not isinstance(dns_latency_raw, (int, float)):
            failures.append(
                CheckFailure("dependency_dns_server", "dns_latency_ms must be numeric when set")
            )
        else:
            dns_latency_ms = float(dns_latency_raw)
            observations["dns_latency_ms"] = dns_latency_ms
            if dns_latency_ms < 0:
                failures.append(
                    CheckFailure("dependency_dns_server", "dns_latency_ms must be >= 0")
                )


def external_status_checks(
    target: TargetConfig,
    failures: list[CheckFailure],
    observations: ObservationMap,
    now_wall_ts: float,
) -> None:
    ext_cfg = target.external
    if ext_cfg.external_status_file is None:
        return

    try:
        raw = ext_cfg.external_status_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        failures.append(
            CheckFailure(
                "semantic_external_status_file",
                f"external status file missing: {ext_cfg.external_status_file}",
            )
        )
        return
    except OSError as exc:
        failures.append(
            CheckFailure(
                "semantic_external_status_file",
                f"cannot read external status file {ext_cfg.external_status_file}: {exc}",
            )
        )
        return

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        failures.append(
            CheckFailure(
                "semantic_external_status_file",
                f"invalid JSON in external status file {ext_cfg.external_status_file}: {exc}",
            )
        )
        return

    if not isinstance(payload, dict):
        failures.append(
            CheckFailure(
                "semantic_external_status_file",
                f"external status file root must be JSON object: {ext_cfg.external_status_file}",
            )
        )
        return

    updated_raw = payload.get("updated_at")
    updated_ts, updated_err = parse_ts(updated_raw, "updated_at")
    updated_age: float | None = None
    if updated_ts is not None:
        updated_age = now_wall_ts - updated_ts
        observations["external_status_updated_age_sec"] = updated_age
    startup_grace_active = updated_age is not None and updated_age <= float(
        ext_cfg.external_status_startup_grace_sec
    )
    observations["external_status_startup_grace_active"] = startup_grace_active
    if ext_cfg.external_status_updated_max_age_sec is not None:
        if updated_err is not None:
            failures.append(CheckFailure("semantic_external_updated_at", updated_err))
        elif updated_ts is None:
            failures.append(
                CheckFailure("semantic_external_updated_at", "updated_at missing timestamp")
            )
        else:
            age = now_wall_ts - updated_ts
            if age > ext_cfg.external_status_updated_max_age_sec:
                failures.append(
                    CheckFailure(
                        "semantic_external_updated_at",
                        (
                            "updated_at stale: "
                            f"age={age:.1f}s max={ext_cfg.external_status_updated_max_age_sec}s"
                        ),
                    )
                )

    if ext_cfg.external_status_last_progress_max_age_sec is not None:
        progress_raw = payload.get("last_progress_ts")
        if (
            progress_raw is None or (isinstance(progress_raw, str) and not progress_raw.strip())
        ) and startup_grace_active:
            pass
        else:
            progress_ts, progress_err = parse_ts(progress_raw, "last_progress_ts")
            if progress_err is not None:
                failures.append(CheckFailure("semantic_external_last_progress_ts", progress_err))
            elif progress_ts is None:
                failures.append(
                    CheckFailure(
                        "semantic_external_last_progress_ts",
                        "last_progress_ts missing timestamp",
                    )
                )
            else:
                progress_age = now_wall_ts - progress_ts
                observations["external_last_progress_age_sec"] = progress_age
                if (
                    progress_age > ext_cfg.external_status_last_progress_max_age_sec
                    and not startup_grace_active
                ):
                    failures.append(
                        CheckFailure(
                            "semantic_external_last_progress_ts",
                            (
                                "last_progress_ts stale: "
                                f"age={progress_age:.1f}s "
                                f"max={ext_cfg.external_status_last_progress_max_age_sec}s"
                            ),
                        )
                    )

    if ext_cfg.external_status_last_success_max_age_sec is not None:
        success_raw = payload.get("last_success_ts")
        if (
            success_raw is None or (isinstance(success_raw, str) and not success_raw.strip())
        ) and startup_grace_active:
            pass
        else:
            success_ts, success_err = parse_ts(success_raw, "last_success_ts")
            if success_err is not None:
                failures.append(CheckFailure("semantic_external_last_success_ts", success_err))
            elif success_ts is None:
                failures.append(
                    CheckFailure(
                        "semantic_external_last_success_ts",
                        "last_success_ts missing timestamp",
                    )
                )
            else:
                success_age = now_wall_ts - success_ts
                observations["external_last_success_age_sec"] = success_age
                if (
                    success_age > ext_cfg.external_status_last_success_max_age_sec
                    and not startup_grace_active
                ):
                    failures.append(
                        CheckFailure(
                            "semantic_external_last_success_ts",
                            (
                                "last_success_ts stale: "
                                f"age={success_age:.1f}s "
                                f"max={ext_cfg.external_status_last_success_max_age_sec}s"
                            ),
                        )
                    )

    internal_state_raw = payload.get("internal_state")
    if internal_state_raw is not None and not isinstance(internal_state_raw, str):
        failures.append(
            CheckFailure(
                "semantic_external_internal_state",
                "internal_state must be string when set",
            )
        )
    elif isinstance(internal_state_raw, str):
        normalized_state = internal_state_raw.strip().lower()
        observations["external_internal_state"] = normalized_state
        unhealthy_values = {v.strip().lower() for v in ext_cfg.external_status_unhealthy_values}
        if normalized_state in unhealthy_values:
            failures.append(
                CheckFailure(
                    "semantic_external_internal_state",
                    f"internal_state is unhealthy: {internal_state_raw}",
                )
            )

    reason_raw = payload.get("reason")
    if isinstance(reason_raw, str):
        observations["external_reason"] = reason_raw
