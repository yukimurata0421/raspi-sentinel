from __future__ import annotations

import errno
import logging
import re
import socket
import ssl
import time
import urllib.error as urllib_error
import urllib.request as urllib_request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from ..config import TargetConfig
from . import command_checks
from .models import ObservationMap

LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class ProbeResult:
    values: ObservationMap = field(default_factory=dict)


def _apply_probe_result(observations: ObservationMap, result: ProbeResult) -> None:
    observations.update(result.values)


def parse_ping_stats(output: str) -> tuple[float | None, float | None]:
    loss_match = re.search(r"(\d+(?:\.\d+)?)%\s+packet loss", output)
    loss_pct = float(loss_match.group(1)) if loss_match else None
    rtt_match = re.search(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)/", output)
    avg_ms = float(rtt_match.group(2)) if rtt_match else None
    return avg_ms, loss_pct


def classify_dns_gaierror(exc: socket.gaierror) -> str:
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


def classify_dns_oserror(exc: OSError) -> str:
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


def classify_http_oserror(exc: OSError, connect_succeeded: bool) -> str:
    if isinstance(exc, ConnectionRefusedError) or exc.errno == errno.ECONNREFUSED:
        return "connection_refused"
    if isinstance(exc, TimeoutError) or exc.errno in (errno.ETIMEDOUT,):
        return "read_timeout" if connect_succeeded else "connect_timeout"
    return "unknown"


def _classify_http_urlerror_reason(reason: object) -> str:
    if isinstance(reason, socket.gaierror):
        return "dns_resolution_failed"
    if isinstance(reason, ssl.SSLError):
        return "tls_error"
    if isinstance(reason, TimeoutError):
        text = str(reason).lower()
        if "read" in text:
            return "read_timeout"
        return "connect_timeout"
    if isinstance(reason, OSError):
        return classify_http_oserror(reason, connect_succeeded=False)

    text = str(reason).lower()
    if "name or service not known" in text or "temporary failure" in text:
        return "dns_resolution_failed"
    if "read timeout" in text:
        return "read_timeout"
    if "connect timeout" in text or "timed out" in text:
        return "connect_timeout"
    return "unknown"


def _init_network_probe_observations(observations: ObservationMap, iface: str) -> None:
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


def _probe_link_layer(iface: str, timeout_sec: int) -> ProbeResult:
    values: ObservationMap = {}
    operstate_path = Path(f"/sys/class/net/{iface}/operstate")
    oper_up: bool | None = None
    oper_raw: str | None = None
    try:
        oper_raw = operstate_path.read_text(encoding="utf-8").strip().lower()
        oper_up = oper_raw == "up"
    except OSError:
        oper_up = None
    values["operstate_raw"] = oper_raw
    values["iface_up"] = oper_up

    addr_result, addr_error = command_checks.run_command_capture(
        ["ip", "-4", "-o", "addr", "show", "dev", iface], timeout_sec=timeout_sec
    )
    has_ipv4: bool | None = None
    if addr_result is not None:
        has_ipv4 = bool(addr_result.stdout.strip())
    elif addr_error == "timeout":
        has_ipv4 = False
    values["ip_assigned"] = has_ipv4

    wifi_connected: bool | None = None
    iw_result, _ = command_checks.run_command_capture(["iw", "dev", iface, "link"], timeout_sec)
    if iw_result is not None:
        iw_output = iw_result.stdout.strip()
        wifi_connected = "Not connected." not in iw_output
        if wifi_connected:
            m = re.search(r"SSID:\s*(.+)", iw_output)
            if m:
                values["ssid"] = m.group(1).strip()
            m = re.search(r"Connected to\s+([0-9A-Fa-f:]{17})", iw_output)
            if m:
                values["bssid"] = m.group(1).lower()
            m = re.search(r"signal:\s*(-?\d+(?:\.\d+)?)\s*dBm", iw_output)
            if m:
                values["rssi_dbm"] = float(m.group(1))
            m = re.search(r"tx bitrate:\s*(\d+(?:\.\d+)?)\s*MBit/s", iw_output)
            if m:
                values["tx_bitrate_mbps"] = float(m.group(1))
            m = re.search(r"rx bitrate:\s*(\d+(?:\.\d+)?)\s*MBit/s", iw_output)
            if m:
                values["rx_bitrate_mbps"] = float(m.group(1))
    values["wifi_associated"] = wifi_connected

    if oper_up is False or has_ipv4 is False or wifi_connected is False:
        values["link_ok"] = False
    elif oper_up is True and has_ipv4 is True and (wifi_connected in (True, None)):
        values["link_ok"] = True

    return ProbeResult(values=values)


def _probe_route(iface: str, timeout_sec: int) -> tuple[ProbeResult, str | None, str | None]:
    values: ObservationMap = {}
    gateway_ip: str | None = None
    route_iface: str | None = None

    route_result, _ = command_checks.run_command_capture(
        ["ip", "-4", "route", "show", "default"], timeout_sec
    )
    if route_result is not None:
        route_text = route_result.stdout.strip()
        if route_text:
            values["route_table_snapshot"] = route_text[:500]
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
                if gateway_ip is None and not iface_matched:
                    gateway_ip = cand_gateway
                    route_iface = cand_iface

            if not has_default_route:
                values["default_route_ok"] = False
                values["route_error_kind"] = "no_default_route"
            elif not iface_matched:
                values["default_route_ok"] = False
                values["route_error_kind"] = "iface_mismatch"
            elif gateway_ip is None:
                values["default_route_ok"] = False
                values["route_error_kind"] = "gateway_ip_missing"
            else:
                values["default_route_ok"] = True
        else:
            values["default_route_ok"] = False
            values["route_error_kind"] = "no_default_route"

    if route_iface is not None:
        values["default_route_iface"] = route_iface
    if gateway_ip is not None:
        values["gateway_ip"] = gateway_ip

    return ProbeResult(values=values), gateway_ip, route_iface


def _probe_gateway(
    target: TargetConfig,
    iface: str,
    gateway_ip: str | None,
    route_iface: str | None,
    timeout_sec: int,
) -> ProbeResult:
    values: ObservationMap = {}
    if gateway_ip is None:
        return ProbeResult(values=values)

    network_cfg = target.network
    gateway_latency_threshold = network_cfg.latency_thresholds_ms.get("gateway")
    gateway_loss_threshold = network_cfg.packet_loss_thresholds_pct.get("gateway")

    neigh_result, _ = command_checks.run_command_capture(
        ["ip", "neigh", "show", gateway_ip, "dev", route_iface or iface], timeout_sec
    )
    if neigh_result is not None:
        neigh_text = neigh_result.stdout.strip().lower()
        if neigh_text:
            resolved = all(state not in neigh_text for state in ("failed", "incomplete"))
            values["neighbor_resolved"] = resolved
            values["arp_gateway_ok"] = resolved

    ping_result, ping_error = command_checks.run_command_capture(
        ["ping", "-n", "-c", "3", "-W", str(timeout_sec), gateway_ip],
        timeout_sec=max(2, timeout_sec * 2),
    )
    if ping_result is not None:
        latency_ms, loss_pct = parse_ping_stats(
            (ping_result.stdout or "") + "\n" + (ping_result.stderr or "")
        )
        values["gateway_latency_ms"] = latency_ms
        values["gateway_packet_loss_pct"] = loss_pct
        values["gateway_ok"] = ping_result.returncode == 0
        if ping_result.returncode != 0:
            if values.get("neighbor_resolved") is False:
                values["gateway_error_kind"] = "neighbor_unresolved"
            elif (
                loss_pct is not None
                and gateway_loss_threshold is not None
                and loss_pct >= gateway_loss_threshold
            ):
                values["gateway_error_kind"] = "high_loss"
            elif (
                latency_ms is not None
                and gateway_latency_threshold is not None
                and latency_ms >= gateway_latency_threshold
            ):
                values["gateway_error_kind"] = "high_latency"
    elif ping_error == "timeout":
        values["gateway_ok"] = False
        values["gateway_error_kind"] = "probe_timeout"

    return ProbeResult(values=values)


def _probe_internet(target: TargetConfig, timeout_sec: int) -> ProbeResult:
    values: ObservationMap = {}
    network_cfg = target.network
    internet_targets = network_cfg.internet_ip_targets or ["1.1.1.1", "8.8.8.8"]
    internet_attempted = False
    internet_attempt_count = 0
    internet_failed_count = 0
    internet_total_targets = len(internet_targets)
    internet_latency_threshold = network_cfg.latency_thresholds_ms.get("internet_ip")
    internet_loss_threshold = network_cfg.packet_loss_thresholds_pct.get("internet_ip")

    for ip_target in internet_targets:
        ping_result, ping_error = command_checks.run_command_capture(
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
        latency_ms, loss_pct = parse_ping_stats((ping_result.stdout or "") + "\n")
        if ping_result.returncode == 0:
            values["internet_ip_ok"] = True
            values["internet_ip_target"] = ip_target
            values["internet_ip_latency_ms"] = latency_ms
            values["internet_ip_packet_loss_pct"] = loss_pct
            break
        internet_failed_count += 1
        if values.get("internet_ip_ok") is not True:
            values["internet_ip_target"] = ip_target
            values["internet_ip_latency_ms"] = latency_ms
            values["internet_ip_packet_loss_pct"] = loss_pct
            values["internet_ip_ok"] = False

    if values.get("internet_ip_ok") is None and internet_attempted:
        values["internet_ip_ok"] = False

    if values.get("internet_ip_ok") is False:
        wan_latency = values.get("internet_ip_latency_ms")
        wan_loss = values.get("internet_ip_packet_loss_pct")
        if (
            isinstance(wan_loss, (int, float))
            and internet_loss_threshold is not None
            and float(wan_loss) >= internet_loss_threshold
        ):
            values["wan_error_kind"] = "high_loss"
        elif (
            isinstance(wan_latency, (int, float))
            and internet_latency_threshold is not None
            and float(wan_latency) >= internet_latency_threshold
        ):
            values["wan_error_kind"] = "high_latency"
        elif internet_attempt_count > 0 and internet_failed_count >= internet_total_targets:
            values["wan_error_kind"] = "all_targets_failed"
        else:
            values["wan_error_kind"] = "partial_targets_failed"

    return ProbeResult(values=values)


def _probe_dns(target: TargetConfig) -> ProbeResult:
    values: ObservationMap = {}
    network_cfg = target.network
    dns_target = network_cfg.dns_query_target or "example.com"
    values["dns_query_target"] = dns_target

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
        values["dns_server"] = nameservers[0]

    dns_start = time.monotonic()
    try:
        if resolv_conf_loaded and not nameservers:
            values["dns_ok"] = False
            values["dns_error_kind"] = "resolver_config_missing"
        else:
            try:
                socket.getaddrinfo(dns_target, 443, type=socket.SOCK_STREAM)
                values["dns_ok"] = True
            except socket.gaierror as exc:
                values["dns_ok"] = False
                values["dns_error_kind"] = classify_dns_gaierror(exc)
            except TimeoutError:
                values["dns_ok"] = False
                values["dns_error_kind"] = "timeout"
            except OSError as exc:
                values["dns_ok"] = False
                values["dns_error_kind"] = classify_dns_oserror(exc)
    finally:
        values["dns_latency_ms"] = (time.monotonic() - dns_start) * 1000.0

    return ProbeResult(values=values)


def _probe_http(target: TargetConfig, timeout_sec: int) -> ProbeResult:
    values: ObservationMap = {}
    network_cfg = target.network
    time_cfg = target.time_health
    http_target = network_cfg.http_probe_target or time_cfg.http_time_probe_url
    if not http_target:
        return ProbeResult(values=values)

    values["http_probe_target"] = http_target
    parsed = urlparse(http_target)
    host = parsed.hostname
    scheme = parsed.scheme.lower()
    if not (host and scheme in ("http", "https")):
        return ProbeResult(values=values)

    total_start = time.monotonic()
    context: ssl.SSLContext | None = ssl.create_default_context() if scheme == "https" else None
    request = urllib_request.Request(
        http_target,
        headers={"User-Agent": "raspi-sentinel", "Connection": "close"},
        method="HEAD",
    )
    try:
        with urllib_request.urlopen(request, timeout=timeout_sec, context=context) as response:
            status_code = int(response.getcode())
            values["http_status_code"] = status_code
            if 200 <= status_code < 300:
                values["http_probe_ok"] = True
            else:
                values["http_probe_ok"] = False
                values["http_error_kind"] = "non_2xx"
    except urllib_error.HTTPError as exc:
        values["http_status_code"] = int(exc.code)
        values["http_probe_ok"] = False
        values["http_error_kind"] = "non_2xx"
    except urllib_error.URLError as exc:
        values["http_probe_ok"] = False
        values["http_error_kind"] = _classify_http_urlerror_reason(exc.reason)
    except socket.gaierror:
        values["http_probe_ok"] = False
        values["http_error_kind"] = "dns_resolution_failed"
    except ssl.SSLError:
        values["http_probe_ok"] = False
        values["http_error_kind"] = "tls_error"
    except OSError as exc:
        values["http_probe_ok"] = False
        values["http_error_kind"] = classify_http_oserror(exc, connect_succeeded=False)
    finally:
        values["http_total_latency_ms"] = (time.monotonic() - total_start) * 1000.0

    return ProbeResult(values=values)


def probe_network_uplink(target: TargetConfig, observations: ObservationMap) -> None:
    network_cfg = target.network
    if not network_cfg.network_probe_enabled or not network_cfg.network_interface:
        return

    iface = network_cfg.network_interface
    timeout_sec = max(1, network_cfg.gateway_probe_timeout_sec)

    _init_network_probe_observations(observations, iface)

    _apply_probe_result(observations, _probe_link_layer(iface=iface, timeout_sec=timeout_sec))
    route_result, gateway_ip, route_iface = _probe_route(iface=iface, timeout_sec=timeout_sec)
    _apply_probe_result(observations, route_result)
    _apply_probe_result(
        observations,
        _probe_gateway(
            target=target,
            iface=iface,
            gateway_ip=gateway_ip,
            route_iface=route_iface,
            timeout_sec=timeout_sec,
        ),
    )
    _apply_probe_result(observations, _probe_internet(target=target, timeout_sec=timeout_sec))
    _apply_probe_result(observations, _probe_dns(target=target))
    _apply_probe_result(observations, _probe_http(target=target, timeout_sec=timeout_sec))
