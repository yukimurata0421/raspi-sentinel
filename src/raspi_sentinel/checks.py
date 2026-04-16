from __future__ import annotations

import errno
import json
import logging
import re
import shlex
import socket
import ssl
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import TargetConfig
from .state_helpers import safe_optional_int
from .state_models import TargetState

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


def _file_freshness_check(
    path: Path,
    max_age_sec: int,
    check_name: str,
    now_wall_ts: float | None = None,
) -> CheckFailure | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return CheckFailure(check_name, f"file missing: {path}")
    except OSError as exc:
        return CheckFailure(check_name, f"cannot stat file {path}: {exc}")

    wall = time.time() if now_wall_ts is None else now_wall_ts
    age = wall - stat.st_mtime
    if age > max_age_sec:
        return CheckFailure(
            check_name,
            f"file stale: {path} age={age:.1f}s max={max_age_sec}s",
        )
    return None


def _command_check(
    command: str,
    timeout_sec: int,
    check_name: str = "command",
    use_shell: bool = False,
) -> CheckFailure | None:
    # Keep shell execution as explicit opt-in.
    if not use_shell and any(token in command for token in ("|", "&&", "||", ";", "$(", "`")):
        return CheckFailure(
            check_name,
            "shell syntax detected; set *_use_shell=true for this command",
        )

    args: str | list[str]
    if use_shell:
        args = command
    else:
        try:
            parsed = shlex.split(command)
        except ValueError as exc:
            return CheckFailure(check_name, f"invalid command syntax: {exc}")
        if not parsed:
            return CheckFailure(check_name, "command is empty")
        args = parsed

    try:
        result = subprocess.run(
            args,
            shell=use_shell,
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
    if ts is None:
        return CheckFailure(check_name, f"{key} missing timestamp")

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
        return None, CheckFailure(
            "semantic_stats_file", f"invalid JSON in stats file {path}: {exc}"
        )

    if not isinstance(data, dict):
        return None, CheckFailure(
            "semantic_stats_file", f"stats file root must be JSON object: {path}"
        )
    return data, None


def _stats_checks(
    target: TargetConfig,
    failures: list[CheckFailure],
    observations: dict[str, Any],
    now_wall_ts: float,
) -> None:
    if target.stats_file is None:
        return

    stats, failure = _load_stats(target.stats_file)
    if failure is not None:
        failures.append(failure)
        return
    if stats is None:
        return

    now_ts = now_wall_ts
    updated_ts_raw = stats.get("updated_at")
    updated_ts, updated_ts_err = _parse_ts(updated_ts_raw, "updated_at")
    if updated_ts is not None:
        observations["stats_age_sec"] = now_ts - updated_ts
    if target.stats_updated_max_age_sec is not None:
        if updated_ts_err is not None:
            failures.append(CheckFailure("semantic_updated_at", updated_ts_err))
        else:
            if updated_ts is None:
                failures.append(CheckFailure("semantic_updated_at", "updated_at missing timestamp"))
                return
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
                failures.append(
                    CheckFailure("semantic_status", f"status is not healthy: {status_raw}")
                )

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


def _external_status_checks(
    target: TargetConfig,
    failures: list[CheckFailure],
    observations: dict[str, Any],
    now_wall_ts: float,
) -> None:
    if target.external_status_file is None:
        return

    try:
        raw = target.external_status_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        failures.append(
            CheckFailure(
                "semantic_external_status_file",
                f"external status file missing: {target.external_status_file}",
            )
        )
        return
    except OSError as exc:
        failures.append(
            CheckFailure(
                "semantic_external_status_file",
                f"cannot read external status file {target.external_status_file}: {exc}",
            )
        )
        return

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        failures.append(
            CheckFailure(
                "semantic_external_status_file",
                f"invalid JSON in external status file {target.external_status_file}: {exc}",
            )
        )
        return

    if not isinstance(payload, dict):
        failures.append(
            CheckFailure(
                "semantic_external_status_file",
                f"external status file root must be JSON object: {target.external_status_file}",
            )
        )
        return

    updated_raw = payload.get("updated_at")
    updated_ts, updated_err = _parse_ts(updated_raw, "updated_at")
    updated_age: float | None = None
    if updated_ts is not None:
        updated_age = now_wall_ts - updated_ts
        observations["external_status_updated_age_sec"] = updated_age
    startup_grace_active = updated_age is not None and updated_age <= float(
        target.external_status_startup_grace_sec
    )
    observations["external_status_startup_grace_active"] = startup_grace_active
    if target.external_status_updated_max_age_sec is not None:
        if updated_err is not None:
            failures.append(CheckFailure("semantic_external_updated_at", updated_err))
        elif updated_ts is None:
            failures.append(
                CheckFailure("semantic_external_updated_at", "updated_at missing timestamp")
            )
        else:
            age = now_wall_ts - updated_ts
            if age > target.external_status_updated_max_age_sec:
                failures.append(
                    CheckFailure(
                        "semantic_external_updated_at",
                        (
                            "updated_at stale: "
                            f"age={age:.1f}s max={target.external_status_updated_max_age_sec}s"
                        ),
                    )
                )

    if target.external_status_last_progress_max_age_sec is not None:
        progress_raw = payload.get("last_progress_ts")
        if (
            progress_raw is None or (isinstance(progress_raw, str) and not progress_raw.strip())
        ) and startup_grace_active:
            pass
        else:
            progress_ts, progress_err = _parse_ts(progress_raw, "last_progress_ts")
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
                    progress_age > target.external_status_last_progress_max_age_sec
                    and not startup_grace_active
                ):
                    failures.append(
                        CheckFailure(
                            "semantic_external_last_progress_ts",
                            (
                                "last_progress_ts stale: "
                                f"age={progress_age:.1f}s "
                                f"max={target.external_status_last_progress_max_age_sec}s"
                            ),
                        )
                    )

    if target.external_status_last_success_max_age_sec is not None:
        success_raw = payload.get("last_success_ts")
        if (
            success_raw is None or (isinstance(success_raw, str) and not success_raw.strip())
        ) and startup_grace_active:
            pass
        else:
            success_ts, success_err = _parse_ts(success_raw, "last_success_ts")
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
                    success_age > target.external_status_last_success_max_age_sec
                    and not startup_grace_active
                ):
                    failures.append(
                        CheckFailure(
                            "semantic_external_last_success_ts",
                            (
                                "last_success_ts stale: "
                                f"age={success_age:.1f}s "
                                f"max={target.external_status_last_success_max_age_sec}s"
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
        unhealthy_values = {v.strip().lower() for v in target.external_status_unhealthy_values}
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


def _run_command_capture(
    args: list[str],
    timeout_sec: int,
) -> tuple[subprocess.CompletedProcess[str] | None, str | None]:
    try:
        result = subprocess.run(
            args,
            check=False,
            timeout=timeout_sec,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError:
        return None, "unavailable"
    return result, None


def _parse_ping_stats(output: str) -> tuple[float | None, float | None]:
    loss_match = re.search(r"(\d+(?:\.\d+)?)%\s+packet loss", output)
    loss_pct = float(loss_match.group(1)) if loss_match else None
    rtt_match = re.search(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)/", output)
    avg_ms = float(rtt_match.group(2)) if rtt_match else None
    return avg_ms, loss_pct


def _classify_dns_gaierror(exc: socket.gaierror) -> str:
    if exc.errno == socket.EAI_NONAME:
        return "nxdomain"
    if exc.errno == socket.EAI_AGAIN:
        return "timeout"
    eai_fail = getattr(socket, "EAI_FAIL", None)
    if eai_fail is not None and exc.errno == eai_fail:
        return "no_server"

    message = " ".join(str(part) for part in exc.args).lower()
    if "temporary failure" in message or "timed out" in message:
        return "timeout"
    if "name or service not known" in message or "nodename nor servname provided" in message:
        return "nxdomain"
    if "no servers could be reached" in message or "no name servers" in message:
        return "no_server"
    if any(token in message for token in ("unreachable", "refused", "no route")):
        return "unreachable"
    return "unknown"


def _classify_dns_oserror(exc: OSError) -> str:
    if isinstance(exc, TimeoutError):
        return "timeout"
    if exc.errno in (errno.ETIMEDOUT,):
        return "timeout"
    if exc.errno in (
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
        errno.ENETDOWN,
        errno.EHOSTDOWN,
        errno.ECONNREFUSED,
    ):
        return "unreachable"
    return "unknown"


def _classify_http_oserror(exc: OSError, connect_succeeded: bool) -> str:
    if isinstance(exc, ConnectionRefusedError) or exc.errno == errno.ECONNREFUSED:
        return "connection_refused"
    if isinstance(exc, TimeoutError) or exc.errno in (errno.ETIMEDOUT,):
        return "read_timeout" if connect_succeeded else "connect_timeout"
    return "unknown"


def _probe_network_uplink(target: TargetConfig, observations: dict[str, Any]) -> None:
    if not target.network_probe_enabled or not target.network_interface:
        return

    iface = target.network_interface
    timeout_sec = max(1, target.gateway_probe_timeout_sec)
    observations["network_probe_enabled"] = True
    observations["network_interface"] = iface

    for key in (
        "link_ok",
        "iface_up",
        "wifi_associated",
        "ip_assigned",
        "operstate_raw",
        "default_route_ok",
        "gateway_ok",
        "internet_ip_ok",
        "dns_ok",
        "http_probe_ok",
        "arp_gateway_ok",
        "neighbor_resolved",
        "ssid",
        "bssid",
        "rssi_dbm",
        "tx_bitrate_mbps",
        "rx_bitrate_mbps",
        "default_route_iface",
        "gateway_ip",
        "route_table_snapshot",
        "gateway_latency_ms",
        "gateway_packet_loss_pct",
        "internet_ip_target",
        "internet_ip_latency_ms",
        "internet_ip_packet_loss_pct",
        "dns_server",
        "dns_query_target",
        "dns_latency_ms",
        "dns_error_kind",
        "route_error_kind",
        "gateway_error_kind",
        "wan_error_kind",
        "http_probe_target",
        "http_status_code",
        "http_total_latency_ms",
        "http_connect_latency_ms",
        "http_tls_latency_ms",
        "http_error_kind",
    ):
        observations.setdefault(key, None)

    operstate_path = Path(f"/sys/class/net/{iface}/operstate")
    oper_up: bool | None = None
    oper_raw: str | None = None
    try:
        oper_raw = operstate_path.read_text(encoding="utf-8").strip().lower()
        oper_up = oper_raw == "up"
    except OSError:
        oper_up = None
    observations["operstate_raw"] = oper_raw
    observations["iface_up"] = oper_up

    addr_result, addr_error = _run_command_capture(
        ["ip", "-4", "-o", "addr", "show", "dev", iface], timeout_sec=timeout_sec
    )
    has_ipv4: bool | None = None
    if addr_result is not None:
        has_ipv4 = bool(addr_result.stdout.strip())
    elif addr_error == "timeout":
        has_ipv4 = False
    observations["ip_assigned"] = has_ipv4

    wifi_connected: bool | None = None
    iw_result, _ = _run_command_capture(["iw", "dev", iface, "link"], timeout_sec=timeout_sec)
    if iw_result is not None:
        iw_output = iw_result.stdout.strip()
        wifi_connected = "Not connected." not in iw_output
        if wifi_connected:
            m = re.search(r"SSID:\s*(.+)", iw_output)
            if m:
                observations["ssid"] = m.group(1).strip()
            m = re.search(r"Connected to\s+([0-9A-Fa-f:]{17})", iw_output)
            if m:
                observations["bssid"] = m.group(1).lower()
            m = re.search(r"signal:\s*(-?\d+(?:\.\d+)?)\s*dBm", iw_output)
            if m:
                observations["rssi_dbm"] = float(m.group(1))
            m = re.search(r"tx bitrate:\s*(\d+(?:\.\d+)?)\s*MBit/s", iw_output)
            if m:
                observations["tx_bitrate_mbps"] = float(m.group(1))
            m = re.search(r"rx bitrate:\s*(\d+(?:\.\d+)?)\s*MBit/s", iw_output)
            if m:
                observations["rx_bitrate_mbps"] = float(m.group(1))
    observations["wifi_associated"] = wifi_connected

    if oper_up is False or has_ipv4 is False or wifi_connected is False:
        observations["link_ok"] = False
    elif oper_up is True and has_ipv4 is True and (wifi_connected in (True, None)):
        observations["link_ok"] = True

    route_result, _ = _run_command_capture(["ip", "-4", "route", "show", "default"], timeout_sec)
    gateway_ip: str | None = None
    route_iface: str | None = None
    if route_result is not None:
        route_text = route_result.stdout.strip()
        if route_text:
            observations["route_table_snapshot"] = route_text[:500]
            has_default_route = False
            iface_matched = False
            for line in route_text.splitlines():
                m_no_gateway = re.search(r"default dev (\S+)", line)
                m = re.search(r"default via (\S+) dev (\S+)", line)
                if m_no_gateway:
                    has_default_route = True
                    cand_iface = m_no_gateway.group(1)
                    if cand_iface == iface:
                        iface_matched = True
                        route_iface = cand_iface
                    continue
                if not m:
                    continue
                has_default_route = True
                cand_gateway, cand_iface = m.group(1), m.group(2)
                if cand_iface == iface:
                    iface_matched = True
                    gateway_ip = cand_gateway
                    route_iface = cand_iface
                    break
                if gateway_ip is None:
                    gateway_ip = cand_gateway
                    route_iface = cand_iface

            if not has_default_route:
                observations["default_route_ok"] = False
                observations["route_error_kind"] = "no_default_route"
            elif not iface_matched:
                observations["default_route_ok"] = False
                observations["route_error_kind"] = "iface_mismatch"
            elif gateway_ip is None:
                observations["default_route_ok"] = False
                observations["route_error_kind"] = "gateway_ip_missing"
            else:
                observations["default_route_ok"] = True
        else:
            observations["default_route_ok"] = False
            observations["route_error_kind"] = "no_default_route"

    if route_iface is not None:
        observations["default_route_iface"] = route_iface
    if gateway_ip is not None:
        observations["gateway_ip"] = gateway_ip

    if gateway_ip is not None:
        gateway_latency_threshold = target.latency_thresholds_ms.get("gateway")
        gateway_loss_threshold = target.packet_loss_thresholds_pct.get("gateway")
        neigh_result, _ = _run_command_capture(
            ["ip", "neigh", "show", gateway_ip, "dev", route_iface or iface], timeout_sec
        )
        if neigh_result is not None:
            neigh_text = neigh_result.stdout.strip().lower()
            if neigh_text:
                resolved = all(state not in neigh_text for state in ("failed", "incomplete"))
                observations["neighbor_resolved"] = resolved
                observations["arp_gateway_ok"] = resolved

        ping_result, ping_error = _run_command_capture(
            ["ping", "-n", "-c", "3", "-W", str(timeout_sec), gateway_ip],
            timeout_sec=max(2, timeout_sec * 2),
        )
        if ping_result is not None:
            latency_ms, loss_pct = _parse_ping_stats(
                (ping_result.stdout or "") + "\n" + (ping_result.stderr or "")
            )
            observations["gateway_latency_ms"] = latency_ms
            observations["gateway_packet_loss_pct"] = loss_pct
            observations["gateway_ok"] = ping_result.returncode == 0
            if ping_result.returncode != 0:
                if observations.get("neighbor_resolved") is False:
                    observations["gateway_error_kind"] = "neighbor_unresolved"
                elif (
                    loss_pct is not None
                    and gateway_loss_threshold is not None
                    and loss_pct >= gateway_loss_threshold
                ):
                    observations["gateway_error_kind"] = "high_loss"
                elif (
                    latency_ms is not None
                    and gateway_latency_threshold is not None
                    and latency_ms >= gateway_latency_threshold
                ):
                    observations["gateway_error_kind"] = "high_latency"
        elif ping_error == "timeout":
            observations["gateway_ok"] = False
            observations["gateway_error_kind"] = "probe_timeout"

    internet_targets = target.internet_ip_targets or ["1.1.1.1", "8.8.8.8"]
    internet_attempted = False
    internet_attempt_count = 0
    internet_failed_count = 0
    internet_total_targets = len(internet_targets)
    internet_latency_threshold = target.latency_thresholds_ms.get("internet_ip")
    internet_loss_threshold = target.packet_loss_thresholds_pct.get("internet_ip")
    for ip_target in internet_targets:
        ping_result, ping_error = _run_command_capture(
            ["ping", "-n", "-c", "3", "-W", str(timeout_sec), ip_target],
            timeout_sec=max(2, timeout_sec * 2),
        )
        if ping_result is None:
            if ping_error == "timeout":
                internet_attempted = True
                internet_attempt_count += 1
                internet_failed_count += 1
            continue
        internet_attempted = True
        internet_attempt_count += 1
        latency_ms, loss_pct = _parse_ping_stats((ping_result.stdout or "") + "\n")
        if ping_result.returncode == 0:
            observations["internet_ip_ok"] = True
            observations["internet_ip_target"] = ip_target
            observations["internet_ip_latency_ms"] = latency_ms
            observations["internet_ip_packet_loss_pct"] = loss_pct
            break
        internet_failed_count += 1
        if observations.get("internet_ip_ok") is not True:
            observations["internet_ip_target"] = ip_target
            observations["internet_ip_latency_ms"] = latency_ms
            observations["internet_ip_packet_loss_pct"] = loss_pct
            observations["internet_ip_ok"] = False
    if observations.get("internet_ip_ok") is None and internet_attempted:
        observations["internet_ip_ok"] = False
    if observations.get("internet_ip_ok") is False:
        wan_latency = observations.get("internet_ip_latency_ms")
        wan_loss = observations.get("internet_ip_packet_loss_pct")
        if (
            isinstance(wan_loss, (int, float))
            and internet_loss_threshold is not None
            and float(wan_loss) >= internet_loss_threshold
        ):
            observations["wan_error_kind"] = "high_loss"
        elif (
            isinstance(wan_latency, (int, float))
            and internet_latency_threshold is not None
            and float(wan_latency) >= internet_latency_threshold
        ):
            observations["wan_error_kind"] = "high_latency"
        elif internet_attempt_count > 0 and internet_failed_count >= internet_total_targets:
            observations["wan_error_kind"] = "all_targets_failed"
        else:
            observations["wan_error_kind"] = "partial_targets_failed"

    dns_target = target.dns_query_target or "example.com"
    observations["dns_query_target"] = dns_target
    nameservers: list[str] = []
    resolv_conf_loaded = True
    try:
        resolv_conf = Path("/etc/resolv.conf").read_text(encoding="utf-8")
    except OSError:
        resolv_conf = ""
        resolv_conf_loaded = False
    for line in resolv_conf.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[0] == "nameserver":
            nameservers.append(parts[1])
    if nameservers:
        observations["dns_server"] = nameservers[0]

    dns_start = time.monotonic()
    try:
        if resolv_conf_loaded and not nameservers:
            observations["dns_ok"] = False
            observations["dns_error_kind"] = "resolver_config_missing"
        else:
            try:
                socket.getaddrinfo(dns_target, 443, type=socket.SOCK_STREAM)
                observations["dns_ok"] = True
            except socket.gaierror as exc:
                observations["dns_ok"] = False
                observations["dns_error_kind"] = _classify_dns_gaierror(exc)
            except TimeoutError:
                observations["dns_ok"] = False
                observations["dns_error_kind"] = "timeout"
            except OSError as exc:
                observations["dns_ok"] = False
                observations["dns_error_kind"] = _classify_dns_oserror(exc)
    finally:
        observations["dns_latency_ms"] = (time.monotonic() - dns_start) * 1000.0

    http_target = target.http_probe_target or target.http_time_probe_url
    if http_target:
        observations["http_probe_target"] = http_target
        parsed = urlparse(http_target)
        host = parsed.hostname
        scheme = parsed.scheme.lower()
        if host and scheme in ("http", "https"):
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
            port = parsed.port or (443 if scheme == "https" else 80)
            total_start = time.monotonic()
            sock: socket.socket | None = None
            wrapped: socket.socket | None = None
            file_obj: Any = None
            try:
                addr_info = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
                family, socktype, proto, _, sockaddr = addr_info[0]
                connect_start = time.monotonic()
                sock = socket.socket(family, socktype, proto)
                sock.settimeout(float(timeout_sec))
                connect_succeeded = False
                try:
                    sock.connect(sockaddr)
                    connect_succeeded = True
                    observations["http_connect_latency_ms"] = (
                        time.monotonic() - connect_start
                    ) * 1000.0
                    wrapped = sock
                    if scheme == "https":
                        tls_start = time.monotonic()
                        context = ssl.create_default_context()
                        wrapped = context.wrap_socket(sock, server_hostname=host)
                        observations["http_tls_latency_ms"] = (
                            time.monotonic() - tls_start
                        ) * 1000.0
                    request_bytes = (
                        f"HEAD {path} HTTP/1.1\r\n"
                        f"Host: {host}\r\n"
                        "Connection: close\r\n"
                        "User-Agent: raspi-sentinel\r\n\r\n"
                    ).encode("ascii", errors="ignore")
                    wrapped.sendall(request_bytes)
                    file_obj = wrapped.makefile("rb")
                    status_line = file_obj.readline(512).decode("iso-8859-1", errors="ignore")
                    m = re.match(r"HTTP/\d\.\d\s+(\d{3})", status_line.strip())
                    if m:
                        status_code = int(m.group(1))
                        observations["http_status_code"] = status_code
                        if 200 <= status_code < 300:
                            observations["http_probe_ok"] = True
                        else:
                            observations["http_probe_ok"] = False
                            observations["http_error_kind"] = "non_2xx"
                    else:
                        observations["http_probe_ok"] = False
                        observations["http_error_kind"] = "unknown"
                except TimeoutError:
                    observations["http_probe_ok"] = False
                    observations["http_error_kind"] = (
                        "read_timeout" if connect_succeeded else "connect_timeout"
                    )
                except ssl.SSLError:
                    observations["http_probe_ok"] = False
                    observations["http_error_kind"] = "tls_error"
                except OSError as exc:
                    observations["http_probe_ok"] = False
                    observations["http_error_kind"] = _classify_http_oserror(
                        exc,
                        connect_succeeded=connect_succeeded,
                    )
                finally:
                    if file_obj is not None:
                        file_obj.close()
                    if wrapped is not None:
                        wrapped.close()
                    elif sock is not None:
                        sock.close()
            except socket.gaierror:
                observations["http_probe_ok"] = False
                observations["http_error_kind"] = "dns_resolution_failed"
            except TimeoutError:
                observations["http_probe_ok"] = False
                observations["http_error_kind"] = "connect_timeout"
            except OSError as exc:
                observations["http_probe_ok"] = False
                observations["http_error_kind"] = _classify_http_oserror(
                    exc,
                    connect_succeeded=False,
                )
            finally:
                observations["http_total_latency_ms"] = (time.monotonic() - total_start) * 1000.0


def apply_records_progress_check(
    target: TargetConfig,
    target_state: TargetState,
    result: CheckResult,
) -> None:
    """Detect stalled ``records_processed_total`` in semantic stats (same cycle as other checks)."""
    stall_cycles_threshold = target.stats_records_stall_cycles
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


def run_checks(target: TargetConfig, now_wall_ts: float | None = None) -> CheckResult:
    failures: list[CheckFailure] = []
    observations: dict[str, Any] = {}
    wall = time.time() if now_wall_ts is None else now_wall_ts

    def _run_dependency_check(
        command: str | None,
        use_shell: bool,
        observation_key: str,
        check_name: str,
    ) -> None:
        if not command:
            return
        timeout_sec = target.dependency_check_timeout_sec or 10
        failure = _command_check(
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
        failure = _file_freshness_check(
            target.heartbeat_file,
            target.heartbeat_max_age_sec,
            "heartbeat_file",
            now_wall_ts=wall,
        )
        if failure:
            failures.append(failure)

    if target.output_file is not None and target.output_max_age_sec is not None:
        failure = _file_freshness_check(
            target.output_file,
            target.output_max_age_sec,
            "output_file",
            now_wall_ts=wall,
        )
        if failure:
            failures.append(failure)

    if target.command:
        timeout_sec = target.command_timeout_sec or 10
        failure = _command_check(
            target.command,
            timeout_sec,
            check_name="command",
            use_shell=target.command_use_shell,
        )
        if failure:
            failures.append(failure)

    _run_dependency_check(
        command=target.link_check_command,
        use_shell=target.link_check_use_shell,
        observation_key="link_ok",
        check_name="dependency_link",
    )
    _run_dependency_check(
        command=target.default_route_check_command,
        use_shell=target.default_route_check_use_shell,
        observation_key="default_route_ok",
        check_name="dependency_default_route",
    )
    _run_dependency_check(
        command=target.gateway_check_command,
        use_shell=target.gateway_check_use_shell,
        observation_key="gateway_ok",
        check_name="dependency_gateway",
    )
    _run_dependency_check(
        command=target.internet_ip_check_command,
        use_shell=target.internet_ip_check_use_shell,
        observation_key="internet_ip_ok",
        check_name="dependency_internet_ip",
    )
    _run_dependency_check(
        command=target.dns_server_check_command,
        use_shell=target.dns_server_check_use_shell,
        observation_key="dns_server_reachable",
        check_name="dependency_dns_server",
    )
    _run_dependency_check(
        command=target.dns_check_command,
        use_shell=target.dns_check_use_shell,
        observation_key="dns_ok",
        check_name="dependency_dns",
    )
    _run_dependency_check(
        command=target.wan_vs_target_check_command,
        use_shell=target.wan_vs_target_check_use_shell,
        observation_key="wan_vs_target_ok",
        check_name="dependency_wan_target",
    )

    _stats_checks(target=target, failures=failures, observations=observations, now_wall_ts=wall)
    _external_status_checks(
        target=target,
        failures=failures,
        observations=observations,
        now_wall_ts=wall,
    )
    _probe_network_uplink(target=target, observations=observations)

    dependency_observation_checks = (
        ("link_ok", "dependency_link"),
        ("default_route_ok", "dependency_default_route"),
        ("gateway_ok", "dependency_gateway"),
        ("internet_ip_ok", "dependency_internet_ip"),
        ("dns_ok", "dependency_dns"),
        ("http_probe_ok", "dependency_http_probe"),
    )
    for observation_key, check_name in dependency_observation_checks:
        if observations.get(observation_key) is False:
            _append_dependency_failure(check_name, f"{observation_key}=false")

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
