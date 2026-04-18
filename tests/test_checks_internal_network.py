from __future__ import annotations

import errno
from pathlib import Path
from typing import Any

from checks_internal_branches_helpers import target

from raspi_sentinel import checks


def test_network_probe_unavailable_commands_are_graceful(monkeypatch: Any) -> None:
    def unavailable_run(*_: Any, **__: Any) -> Any:
        raise OSError("command unavailable")

    def unavailable_getaddrinfo(*_: Any, **__: Any) -> Any:
        raise OSError("dns unavailable")

    def unavailable_urlopen(*_: Any, **__: Any) -> Any:
        raise checks.urllib_error.URLError(OSError("dns unavailable"))

    monkeypatch.setattr(checks.subprocess, "run", unavailable_run)
    monkeypatch.setattr(checks.socket, "getaddrinfo", unavailable_getaddrinfo)
    monkeypatch.setattr(checks.urllib_request, "urlopen", unavailable_urlopen)
    result = checks.run_checks(
        target(
            network_probe_enabled=True,
            network_interface="wlan999",
            dns_query_target="example.invalid",
            http_probe_target="https://example.invalid",
        )
    )
    # Probe failures should be represented as unknown (None), not forced false.
    assert result.observations.get("link_ok") is None
    assert result.observations.get("default_route_ok") is None
    assert result.observations.get("gateway_ok") is None
    assert result.observations.get("internet_ip_ok") is None


def test_network_http_probe_non_2xx_is_failure(monkeypatch: Any) -> None:
    def fake_run_command_capture(args: list[str], timeout_sec: int) -> tuple[Any, Any]:
        return None, "unavailable"

    monkeypatch.setattr(checks, "_run_command_capture", fake_run_command_capture)
    monkeypatch.setattr(
        checks.urllib_request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            checks.urllib_error.HTTPError(
                url="http://probe.example/health",
                code=503,
                msg="Service Unavailable",
                hdrs=None,
                fp=None,
            )
        ),
    )
    monkeypatch.setattr(
        checks.Path,
        "read_text",
        lambda self, encoding="utf-8": "nameserver 1.1.1.1\n",
        raising=False,
    )

    result = checks.run_checks(
        target(
            network_probe_enabled=True,
            network_interface="wlan0",
            dns_query_target="dns.example",
            http_probe_target="http://probe.example/health",
        )
    )
    assert result.observations["http_status_code"] == 503
    assert result.observations["http_probe_ok"] is False
    assert result.observations["http_error_kind"] == "non_2xx"


def test_network_http_error_kind_distinguishes_dns_connect_read_timeout_refused_tls(
    monkeypatch: Any,
) -> None:
    cases = [
        ("dns_resolution_failed", "http://probe.example/health"),
        ("connect_timeout", "http://probe.example/health"),
        ("read_timeout", "http://probe.example/health"),
        ("connection_refused", "http://probe.example/health"),
        ("tls_error", "https://probe.example/health"),
    ]

    for expected_kind, http_target in cases:
        with monkeypatch.context() as m:
            m.setattr(
                checks.Path,
                "read_text",
                lambda self, encoding="utf-8": "nameserver 1.1.1.1\n",
                raising=False,
            )
            m.setattr(
                checks,
                "_run_command_capture",
                lambda args, timeout_sec: (None, "unavailable"),
            )
            if expected_kind == "dns_resolution_failed":
                reason: object = checks.socket.gaierror(checks.socket.EAI_NONAME, "name not known")
            elif expected_kind == "connect_timeout":
                reason = TimeoutError("connect timeout")
            elif expected_kind == "read_timeout":
                reason = TimeoutError("read timeout")
            elif expected_kind == "connection_refused":
                reason = ConnectionRefusedError(errno.ECONNREFUSED, "refused")
            else:
                reason = checks.ssl.SSLError("tls failed")

            m.setattr(
                checks.urllib_request,
                "urlopen",
                lambda *args, **kwargs: (_ for _ in ()).throw(checks.urllib_error.URLError(reason)),
            )

            result = checks.run_checks(
                target(
                    network_probe_enabled=True,
                    network_interface="wlan0",
                    dns_query_target="dns.example",
                    http_probe_target=http_target,
                )
            )
            assert result.observations["http_probe_ok"] is False
            assert result.observations["http_error_kind"] == expected_kind


def test_network_dns_error_kind_classifies_resolver_missing_no_server_unreachable_unknown(
    monkeypatch: Any,
) -> None:
    base_target = target(
        network_probe_enabled=True,
        network_interface="wlan0",
        dns_query_target="dns.example",
        http_probe_target=None,
    )

    cases = [
        ("resolver_config_missing", "search local\n", None),
        (
            "no_server",
            "nameserver 1.1.1.1\n",
            checks.socket.gaierror(
                getattr(checks.socket, "EAI_FAIL", checks.socket.EAI_AGAIN),
                "no servers",
            ),
        ),
        (
            "unreachable",
            "nameserver 1.1.1.1\n",
            OSError(errno.ENETUNREACH, "network unreachable"),
        ),
        (
            "unknown",
            "nameserver 1.1.1.1\n",
            OSError(errno.EPERM, "permission denied"),
        ),
        (
            "nxdomain",
            "nameserver 1.1.1.1\n",
            checks.socket.gaierror(checks.socket.EAI_NONAME, "name not known"),
        ),
        (
            "timeout",
            "nameserver 1.1.1.1\n",
            checks.socket.gaierror(checks.socket.EAI_AGAIN, "temporary failure"),
        ),
    ]

    for expected_kind, resolv_conf_text, injected_error in cases:
        with monkeypatch.context() as m:
            m.setattr(
                checks,
                "_run_command_capture",
                lambda args, timeout_sec: (None, "unavailable"),
            )

            def fake_read_text(self: Path, encoding: str = "utf-8") -> str:
                if str(self) == "/etc/resolv.conf":
                    return resolv_conf_text
                raise OSError("unavailable")

            m.setattr(checks.Path, "read_text", fake_read_text, raising=False)

            def fake_getaddrinfo(host: str, port: int, type: int = 0) -> list[tuple[Any, ...]]:
                if injected_error is not None:
                    raise injected_error
                return [
                    (
                        checks.socket.AF_INET,
                        checks.socket.SOCK_STREAM,
                        6,
                        "",
                        ("127.0.0.1", port),
                    )
                ]

            m.setattr(checks.socket, "getaddrinfo", fake_getaddrinfo)

            result = checks.run_checks(base_target)
            assert result.observations["dns_ok"] is False
            assert result.observations["dns_error_kind"] == expected_kind


def test_network_link_ok_exposes_iface_up_wifi_associated_ip_assigned(monkeypatch: Any) -> None:
    class Result:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout
            self.stderr = ""
            self.returncode = 0

    def fake_run_command_capture(args: list[str], timeout_sec: int) -> tuple[Any, Any]:
        if args[:4] == ["ip", "-4", "-o", "addr"]:
            return Result("2: wlan0    inet 192.168.0.2/24"), None
        if args[:3] == ["iw", "dev", "wlan0"]:
            return Result("Not connected."), None
        return None, "unavailable"

    def fake_read_text(self: Path, encoding: str = "utf-8") -> str:
        if str(self).endswith("/operstate"):
            return "down\n"
        if str(self) == "/etc/resolv.conf":
            return "nameserver 1.1.1.1\n"
        raise OSError("unavailable")

    monkeypatch.setattr(checks, "_run_command_capture", fake_run_command_capture)
    monkeypatch.setattr(checks.Path, "read_text", fake_read_text, raising=False)
    monkeypatch.setattr(
        checks.socket,
        "getaddrinfo",
        lambda host, port, type=0: [
            (checks.socket.AF_INET, checks.socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))
        ],
    )

    result = checks.run_checks(
        target(
            network_probe_enabled=True,
            network_interface="wlan0",
            http_probe_target=None,
        )
    )
    assert result.observations["operstate_raw"] == "down"
    assert result.observations["iface_up"] is False
    assert result.observations["wifi_associated"] is False
    assert result.observations["ip_assigned"] is True
    assert result.observations["link_ok"] is False


def test_dns_and_http_error_classifier_helpers() -> None:
    assert (
        checks._classify_dns_gaierror(
            checks.socket.gaierror(checks.socket.EAI_NONAME, "name or service not known")
        )
        == "nxdomain"
    )
    assert (
        checks._classify_dns_gaierror(
            checks.socket.gaierror(checks.socket.EAI_AGAIN, "temporary failure in name resolution")
        )
        == "timeout"
    )
    assert checks._classify_dns_gaierror(
        checks.socket.gaierror(-9999, "no servers could be reached")
    ) == ("no_server")
    assert checks._classify_dns_gaierror(checks.socket.gaierror(-9999, "network unreachable")) == (
        "unreachable"
    )
    assert checks._classify_dns_gaierror(checks.socket.gaierror(-9999, "boom")) == "unknown"

    assert checks._classify_dns_oserror(TimeoutError("x")) == "timeout"
    assert checks._classify_dns_oserror(OSError(errno.ENETUNREACH, "x")) == "unreachable"
    assert checks._classify_dns_oserror(OSError(errno.EPERM, "x")) == "unknown"

    assert (
        checks._classify_http_oserror(ConnectionRefusedError(errno.ECONNREFUSED, "x"), False)
        == "connection_refused"
    )
    assert checks._classify_http_oserror(TimeoutError("x"), False) == "connect_timeout"
    assert checks._classify_http_oserror(TimeoutError("x"), True) == "read_timeout"
    assert checks._classify_http_oserror(OSError(errno.EPERM, "x"), True) == "unknown"



def test_network_probe_route_gateway_and_internet_branches(monkeypatch: Any) -> None:
    class Result:
        def __init__(self, stdout: str, returncode: int = 0) -> None:
            self.stdout = stdout
            self.stderr = ""
            self.returncode = returncode

    def fake_read_text(self: Path, encoding: str = "utf-8") -> str:
        if str(self).endswith("/operstate"):
            return "up\n"
        if str(self) == "/etc/resolv.conf":
            return "nameserver 1.1.1.1\n"
        raise OSError("unavailable")

    class DummyHttpResponse:
        def __enter__(self) -> "DummyHttpResponse":
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

        def getcode(self) -> int:
            return 204

    call_state = {"internet_count": 0}

    def fake_run_command_capture(args: list[str], timeout_sec: int) -> tuple[Any, Any]:
        if args[:4] == ["ip", "-4", "-o", "addr"]:
            return Result("2: wlan0    inet 192.168.1.2/24"), None
        if args[:3] == ["iw", "dev", "wlan0"]:
            return Result("Connected to aa:bb:cc:dd:ee:ff\nSSID: test\n"), None
        if args[:5] == ["ip", "-4", "route", "show", "default"]:
            return Result("default via 192.168.1.1 dev wlan0\n"), None
        if args[:3] == ["ip", "neigh", "show"]:
            return Result("192.168.1.1 dev wlan0 lladdr 00:11:22:33:44:55 REACHABLE"), None
        if args[:5] == ["ping", "-n", "-c", "3", "-W"] and args[-1] == "192.168.1.1":
            return Result(
                "3 packets transmitted, 3 received, 0% packet loss\n"
                "rtt min/avg/max/mdev = 1.0/2.0/3.0/0.1 ms",
                returncode=0,
            ), None
        if args[:5] == ["ping", "-n", "-c", "3", "-W"] and args[-1] in ("1.1.1.1", "8.8.8.8"):
            call_state["internet_count"] += 1
            if call_state["internet_count"] == 1:
                return Result(
                    "3 packets transmitted, 0 received, 100% packet loss\n",
                    returncode=1,
                ), None
            return Result(
                "3 packets transmitted, 3 received, 0% packet loss\n"
                "rtt min/avg/max/mdev = 10.0/20.0/30.0/1.0 ms",
                returncode=0,
            ), None
        return None, "unavailable"

    monkeypatch.setattr(checks.Path, "read_text", fake_read_text, raising=False)
    monkeypatch.setattr(checks, "_run_command_capture", fake_run_command_capture)
    monkeypatch.setattr(
        checks.urllib_request,
        "urlopen",
        lambda *args, **kwargs: DummyHttpResponse(),
    )

    result = checks.run_checks(
        target(
            network_probe_enabled=True,
            network_interface="wlan0",
            internet_ip_targets=["1.1.1.1", "8.8.8.8"],
            http_probe_target="http://probe.example/health",
        )
    )
    obs = result.observations
    assert obs["default_route_ok"] is True
    assert obs["gateway_ok"] is True
    assert obs["neighbor_resolved"] is True
    assert obs["arp_gateway_ok"] is True
    assert obs["internet_ip_ok"] is True
    assert obs["internet_ip_target"] == "8.8.8.8"
    assert obs["http_probe_ok"] is True


def test_network_probe_handles_empty_route_and_gateway_timeout(monkeypatch: Any) -> None:
    class Result:
        def __init__(self, stdout: str, returncode: int = 0) -> None:
            self.stdout = stdout
            self.stderr = ""
            self.returncode = returncode

    def fake_read_text(self: Path, encoding: str = "utf-8") -> str:
        if str(self).endswith("/operstate"):
            return "up\n"
        if str(self) == "/etc/resolv.conf":
            return "nameserver 1.1.1.1\n"
        raise OSError("unavailable")

    def fake_run_command_capture(args: list[str], timeout_sec: int) -> tuple[Any, Any]:
        if args[:4] == ["ip", "-4", "-o", "addr"]:
            return Result("2: wlan0    inet 192.168.1.2/24"), None
        if args[:3] == ["iw", "dev", "wlan0"]:
            return Result("Connected to aa:bb:cc:dd:ee:ff\nSSID: test\n"), None
        if args[:5] == ["ip", "-4", "route", "show", "default"]:
            return Result(""), None
        if args[:5] == ["ping", "-n", "-c", "3", "-W"]:
            return None, "timeout"
        return None, "unavailable"

    monkeypatch.setattr(checks.Path, "read_text", fake_read_text, raising=False)
    monkeypatch.setattr(checks, "_run_command_capture", fake_run_command_capture)
    monkeypatch.setattr(
        checks.socket,
        "getaddrinfo",
        lambda host, port, type=0: [
            (checks.socket.AF_INET, checks.socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))
        ],
    )

    result = checks.run_checks(
        target(
            network_probe_enabled=True,
            network_interface="wlan0",
            http_probe_target=None,
        )
    )
    obs = result.observations
    assert obs["default_route_ok"] is False
    assert obs["route_error_kind"] == "no_default_route"
    assert obs["gateway_ok"] is None
    assert obs["internet_ip_ok"] is False
    assert obs["wan_error_kind"] == "all_targets_failed"


def test_network_probe_sets_route_and_gateway_error_kinds(monkeypatch: Any) -> None:
    class Result:
        def __init__(self, stdout: str, returncode: int = 0) -> None:
            self.stdout = stdout
            self.stderr = ""
            self.returncode = returncode

    def fake_read_text(self: Path, encoding: str = "utf-8") -> str:
        if str(self).endswith("/operstate"):
            return "up\n"
        if str(self) == "/etc/resolv.conf":
            return "nameserver 1.1.1.1\n"
        raise OSError("unavailable")

    def fake_run_command_capture(args: list[str], timeout_sec: int) -> tuple[Any, Any]:
        if args[:4] == ["ip", "-4", "-o", "addr"]:
            return Result("2: wlan0    inet 192.168.1.2/24"), None
        if args[:3] == ["iw", "dev", "wlan0"]:
            return Result("Connected to aa:bb:cc:dd:ee:ff\nSSID: test\n"), None
        if args[:5] == ["ip", "-4", "route", "show", "default"]:
            return Result("default via 192.168.1.1 dev eth0\n"), None
        if args[:3] == ["ip", "neigh", "show"]:
            return Result("192.168.1.1 dev eth0 lladdr 00:11:22:33:44:55 INCOMPLETE"), None
        if args[:5] == ["ping", "-n", "-c", "3", "-W"] and args[-1] == "192.168.1.1":
            return Result(
                "3 packets transmitted, 0 received, 100% packet loss\n",
                returncode=1,
            ), None
        if args[:5] == ["ping", "-n", "-c", "3", "-W"]:
            return Result(
                "3 packets transmitted, 0 received, 100% packet loss\n",
                returncode=1,
            ), None
        return None, "unavailable"

    monkeypatch.setattr(checks.Path, "read_text", fake_read_text, raising=False)
    monkeypatch.setattr(checks, "_run_command_capture", fake_run_command_capture)
    monkeypatch.setattr(
        checks.socket,
        "getaddrinfo",
        lambda host, port, type=0: [
            (checks.socket.AF_INET, checks.socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))
        ],
    )

    result = checks.run_checks(
        target(
            network_probe_enabled=True,
            network_interface="wlan0",
            http_probe_target=None,
            internet_ip_targets=["1.1.1.1", "8.8.8.8"],
        )
    )
    obs = result.observations
    assert obs["default_route_ok"] is False
    assert obs["route_error_kind"] == "iface_mismatch"
    assert obs["gateway_error_kind"] == "neighbor_unresolved"
    assert obs["wan_error_kind"] == "all_targets_failed"


