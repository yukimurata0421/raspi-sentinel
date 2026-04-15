from __future__ import annotations

import errno
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from conftest import make_target

from raspi_sentinel import checks


def _target(**overrides: Any) -> Any:
    return make_target(**overrides)


def test_file_freshness_missing_and_stale(tmp_path: Path, monkeypatch: Any) -> None:
    missing = checks._file_freshness_check(tmp_path / "missing.txt", 10, "heartbeat_file")
    assert missing is not None and "missing" in missing.message

    p = tmp_path / "f.txt"
    p.write_text("x", encoding="utf-8")
    st = p.stat()
    monkeypatch.setattr(checks.time, "time", lambda: st.st_mtime + 100)
    stale = checks._file_freshness_check(p, 10, "heartbeat_file")
    assert stale is not None and "stale" in stale.message

    monkeypatch.setattr(checks.time, "time", lambda: st.st_mtime + 1)
    assert checks._file_freshness_check(p, 10, "heartbeat_file") is None


def test_file_freshness_oserror_branch() -> None:
    class DummyPath:
        def stat(self) -> Any:
            raise OSError("boom")

        def __str__(self) -> str:
            return "/dummy"

    failure = checks._file_freshness_check(DummyPath(), 10, "heartbeat_file")  # type: ignore[arg-type]
    assert failure is not None and "cannot stat file" in failure.message


def test_command_check_timeout_oserror_nonzero_and_success(monkeypatch: Any) -> None:
    def timeout_run(*_: Any, **__: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(checks.subprocess, "run", timeout_run)
    assert checks._command_check("x", 1, "command") is not None

    def os_run(*_: Any, **__: Any) -> Any:
        raise OSError("boom")

    monkeypatch.setattr(checks.subprocess, "run", os_run)
    assert checks._command_check("x", 1, "command") is not None

    def bad_run(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess(args=["x"], returncode=2, stdout="", stderr="err")

    monkeypatch.setattr(checks.subprocess, "run", bad_run)
    failure = checks._command_check("x", 1, "command")
    assert failure is not None and "exit code" in failure.message

    def ok_run(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess(args=["x"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(checks.subprocess, "run", ok_run)
    assert checks._command_check("x", 1, "command") is None


def test_command_check_requires_shell_opt_in_for_shell_syntax() -> None:
    failure = checks._command_check("echo ok | cat", 1, "command", use_shell=False)
    assert failure is not None
    assert "*_use_shell=true" in failure.message


def test_service_active_check_all_branches(monkeypatch: Any) -> None:
    def timeout_run(*_: Any, **__: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="systemctl", timeout=10)

    monkeypatch.setattr(checks.subprocess, "run", timeout_run)
    assert checks._service_active_check("svc") is not None

    def os_run(*_: Any, **__: Any) -> Any:
        raise OSError("no systemctl")

    monkeypatch.setattr(checks.subprocess, "run", os_run)
    assert checks._service_active_check("svc") is not None

    def inactive_run(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess(
            args=["systemctl"], returncode=3, stdout="inactive", stderr=""
        )

    monkeypatch.setattr(checks.subprocess, "run", inactive_run)
    assert checks._service_active_check("svc") is not None

    def active_run(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess(args=["systemctl"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(checks.subprocess, "run", active_run)
    assert checks._service_active_check("svc") is None


def test_stats_schema_branches_for_invalid_fields(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).isoformat()
    stats = {
        "updated_at": now,
        "last_input_ts": now,
        "last_success_ts": now,
        "status": 1,
        "records_processed_total": "x",
        "dns_ok": "x",
        "gateway_ok": "x",
    }
    p = tmp_path / "stats.json"
    p.write_text(json.dumps(stats), encoding="utf-8")
    result = checks.run_checks(
        _target(
            stats_file=p,
            stats_updated_max_age_sec=120,
            stats_last_input_max_age_sec=120,
            stats_last_success_max_age_sec=120,
        )
    )
    names = {f.check for f in result.failures}
    assert "semantic_status" in names
    assert "semantic_records_total" in names
    assert "dependency_dns" in names
    assert "dependency_gateway" in names


def test_stats_schema_marks_unhealthy_status_and_false_dependency_flags(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    p = tmp_path / "stats.json"
    p.write_text(
        json.dumps(
            {
                "updated_at": now,
                "status": "degraded",
                "dns_ok": False,
                "gateway_ok": False,
            }
        ),
        encoding="utf-8",
    )
    result = checks.run_checks(_target(stats_file=p, stats_updated_max_age_sec=120))
    names = {f.check for f in result.failures}
    assert "semantic_status" in names
    assert "dependency_dns" in names
    assert "dependency_gateway" in names


def test_stats_file_read_oserror_and_non_object(tmp_path: Path) -> None:
    d = tmp_path / "dir_as_stats"
    d.mkdir()
    result = checks.run_checks(_target(stats_file=d, stats_updated_max_age_sec=10))
    assert any(f.check == "semantic_stats_file" for f in result.failures)

    p = tmp_path / "stats.json"
    p.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    result = checks.run_checks(_target(stats_file=p, stats_updated_max_age_sec=10))
    assert any(f.check == "semantic_stats_file" for f in result.failures)


def test_stats_timestamp_format_branches(tmp_path: Path) -> None:
    p = tmp_path / "stats.json"
    p.write_text(json.dumps({"updated_at": "invalid"}), encoding="utf-8")
    result = checks.run_checks(_target(stats_file=p, stats_updated_max_age_sec=10))
    assert any("invalid timestamp format" in f.message for f in result.failures)

    p.write_text(json.dumps({"updated_at": "2026-04-10T10:00:00"}), encoding="utf-8")
    result = checks.run_checks(_target(stats_file=p, stats_updated_max_age_sec=10))
    assert any("timezone offset" in f.message for f in result.failures)

    now_z = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    p.write_text(json.dumps({"updated_at": now_z}), encoding="utf-8")
    result = checks.run_checks(_target(stats_file=p, stats_updated_max_age_sec=3600))
    assert result.healthy


def test_external_status_internal_state_type_error_branch(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).isoformat()
    p = tmp_path / "external-status.json"
    p.write_text(
        json.dumps(
            {
                "updated_at": now,
                "internal_state": 1,
                "last_progress_ts": now,
                "last_success_ts": now,
                "reason": {"raw": "ignored"},
                "components": {"pubsub": {"status": "failed"}},
            }
        ),
        encoding="utf-8",
    )
    result = checks.run_checks(
        _target(
            external_status_file=p,
            external_status_updated_max_age_sec=60,
            external_status_last_progress_max_age_sec=60,
            external_status_last_success_max_age_sec=60,
        )
    )
    assert any(f.check == "semantic_external_internal_state" for f in result.failures)


def test_stats_last_input_stale_branch(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    p = tmp_path / "stats.json"
    p.write_text(
        json.dumps(
            {
                "updated_at": now.isoformat(),
                "last_input_ts": (now.replace(year=2025)).isoformat(),
                "last_success_ts": now.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    result = checks.run_checks(
        _target(
            stats_file=p,
            stats_updated_max_age_sec=3600,
            stats_last_input_max_age_sec=10,
            stats_last_success_max_age_sec=3600,
        )
    )
    assert any(f.check == "semantic_last_input_ts" for f in result.failures)


def test_run_checks_with_command_dns_gateway_and_service(monkeypatch: Any) -> None:
    calls: list[Any] = []

    def fake_run(cmd: Any, **_: Any) -> Any:
        calls.append(cmd)
        if isinstance(cmd, list):
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess([cmd], 0, "", "")

    monkeypatch.setattr(checks.subprocess, "run", fake_run)
    result = checks.run_checks(
        _target(
            services=["svc"],
            service_active=True,
            command="true",
            command_timeout_sec=1,
            dns_check_command="true",
            gateway_check_command="true",
            dependency_check_timeout_sec=1,
        )
    )
    assert result.healthy
    assert len(calls) >= 4


def test_run_checks_with_extended_dependency_commands(monkeypatch: Any) -> None:
    def fake_run(cmd: Any, **_: Any) -> Any:
        return subprocess.CompletedProcess([cmd], 0, "", "")

    monkeypatch.setattr(checks.subprocess, "run", fake_run)
    result = checks.run_checks(
        _target(
            link_check_command="true",
            default_route_check_command="true",
            internet_ip_check_command="true",
            dns_server_check_command="true",
            wan_vs_target_check_command="true",
            dependency_check_timeout_sec=1,
        )
    )
    assert result.healthy
    assert result.observations["link_ok"] is True
    assert result.observations["default_route_ok"] is True
    assert result.observations["internet_ip_ok"] is True
    assert result.observations["dns_server_reachable"] is True
    assert result.observations["wan_vs_target_ok"] is True


def test_run_checks_heartbeat_output_and_service_failure(tmp_path: Path, monkeypatch: Any) -> None:
    hb = tmp_path / "hb.txt"
    out = tmp_path / "out.txt"
    hb.write_text("ok", encoding="utf-8")
    out.write_text("ok", encoding="utf-8")

    def fake_run(cmd: Any, **_: Any) -> Any:
        if isinstance(cmd, list):
            return subprocess.CompletedProcess(cmd, 1, "inactive", "")
        return subprocess.CompletedProcess([cmd], 0, "", "")

    monkeypatch.setattr(checks.subprocess, "run", fake_run)
    result = checks.run_checks(
        _target(
            services=["svc"],
            service_active=True,
            heartbeat_file=hb,
            heartbeat_max_age_sec=3600,
            output_file=out,
            output_max_age_sec=3600,
            command="true",
            command_timeout_sec=1,
            dns_check_command="true",
            gateway_check_command="true",
            dependency_check_timeout_sec=1,
        )
    )
    assert not result.healthy
    assert any(f.check == "service_active" for f in result.failures)


def test_stats_schema_validates_extended_dependency_fields(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc).isoformat()
    p = tmp_path / "stats.json"
    p.write_text(
        json.dumps(
            {
                "updated_at": now,
                "link_ok": "x",
                "default_route_ok": "x",
                "internet_ip_ok": "x",
                "dns_server_reachable": "x",
                "wan_vs_target_ok": "x",
                "dns_latency_ms": "x",
            }
        ),
        encoding="utf-8",
    )
    result = checks.run_checks(_target(stats_file=p, stats_updated_max_age_sec=120))
    names = {f.check for f in result.failures}
    assert "dependency_link" in names
    assert "dependency_default_route" in names
    assert "dependency_internet_ip" in names
    assert "dependency_dns_server" in names
    assert "dependency_wan_target" in names


def test_network_probe_unavailable_commands_are_graceful(monkeypatch: Any) -> None:
    def unavailable_run(*_: Any, **__: Any) -> Any:
        raise OSError("command unavailable")

    def unavailable_getaddrinfo(*_: Any, **__: Any) -> Any:
        raise OSError("dns unavailable")

    monkeypatch.setattr(checks.subprocess, "run", unavailable_run)
    monkeypatch.setattr(checks.socket, "getaddrinfo", unavailable_getaddrinfo)
    result = checks.run_checks(
        _target(
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
    class DummyFile:
        def readline(self, _: int) -> bytes:
            return b"HTTP/1.1 503 Service Unavailable\r\n"

        def close(self) -> None:
            return None

    class DummySocket:
        def settimeout(self, _: float) -> None:
            return None

        def connect(self, _: Any) -> None:
            return None

        def sendall(self, _: bytes) -> None:
            return None

        def makefile(self, _: str) -> DummyFile:
            return DummyFile()

        def close(self) -> None:
            return None

    def fake_run_command_capture(args: list[str], timeout_sec: int) -> tuple[Any, Any]:
        return None, "unavailable"

    def fake_getaddrinfo(host: str, port: int, type: int = 0) -> list[tuple[Any, ...]]:
        return [(checks.socket.AF_INET, checks.socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(checks, "_run_command_capture", fake_run_command_capture)
    monkeypatch.setattr(checks.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(checks.socket, "socket", lambda *args, **kwargs: DummySocket())
    monkeypatch.setattr(
        checks.Path,
        "read_text",
        lambda self, encoding="utf-8": "nameserver 1.1.1.1\n",
        raising=False,
    )

    result = checks.run_checks(
        _target(
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
    class DummyFile:
        def __init__(
            self,
            status_line: bytes = b"HTTP/1.1 204 No Content\r\n",
            *,
            timeout: bool = False,
        ):
            self._status_line = status_line
            self._timeout = timeout

        def readline(self, _: int) -> bytes:
            if self._timeout:
                raise TimeoutError("read timeout")
            return self._status_line

        def close(self) -> None:
            return None

    class DummySocket:
        def __init__(
            self,
            *,
            connect_timeout: bool = False,
            connection_refused: bool = False,
            read_timeout: bool = False,
        ) -> None:
            self.connect_timeout = connect_timeout
            self.connection_refused = connection_refused
            self.read_timeout = read_timeout

        def settimeout(self, _: float) -> None:
            return None

        def connect(self, _: Any) -> None:
            if self.connect_timeout:
                raise TimeoutError("connect timeout")
            if self.connection_refused:
                raise ConnectionRefusedError(errno.ECONNREFUSED, "refused")

        def sendall(self, _: bytes) -> None:
            return None

        def makefile(self, _: str) -> DummyFile:
            return DummyFile(timeout=self.read_timeout)

        def close(self) -> None:
            return None

    class TlsBrokenContext:
        def wrap_socket(self, sock: Any, server_hostname: str) -> Any:
            raise checks.ssl.SSLError("tls failed")

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

            def fake_getaddrinfo(host: str, port: int, type: int = 0) -> list[tuple[Any, ...]]:
                if host == "probe.example" and expected_kind == "dns_resolution_failed":
                    raise checks.socket.gaierror(checks.socket.EAI_NONAME, "name not known")
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

            if expected_kind == "connect_timeout":
                m.setattr(
                    checks.socket,
                    "socket",
                    lambda *args, **kwargs: DummySocket(connect_timeout=True),
                )
            elif expected_kind == "read_timeout":
                m.setattr(
                    checks.socket,
                    "socket",
                    lambda *args, **kwargs: DummySocket(read_timeout=True),
                )
            elif expected_kind == "connection_refused":
                m.setattr(
                    checks.socket,
                    "socket",
                    lambda *args, **kwargs: DummySocket(connection_refused=True),
                )
            else:
                m.setattr(checks.socket, "socket", lambda *args, **kwargs: DummySocket())

            if expected_kind == "tls_error":
                m.setattr(
                    checks.ssl,
                    "create_default_context",
                    lambda: TlsBrokenContext(),
                )

            result = checks.run_checks(
                _target(
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
    base_target = _target(
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
        _target(
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


def test_run_command_capture_and_ping_parser_branches(monkeypatch: Any) -> None:
    def timeout_run(*_: Any, **__: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(checks.subprocess, "run", timeout_run)
    result, error = checks._run_command_capture(["echo", "x"], timeout_sec=1)
    assert result is None and error == "timeout"

    def os_run(*_: Any, **__: Any) -> Any:
        raise OSError("missing")

    monkeypatch.setattr(checks.subprocess, "run", os_run)
    result, error = checks._run_command_capture(["echo", "x"], timeout_sec=1)
    assert result is None and error == "unavailable"

    def ok_run(*_: Any, **__: Any) -> Any:
        return subprocess.CompletedProcess(args=["echo"], returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(checks.subprocess, "run", ok_run)
    result, error = checks._run_command_capture(["echo", "x"], timeout_sec=1)
    assert result is not None and error is None

    latency, loss = checks._parse_ping_stats(
        (
            "3 packets transmitted, 3 received, 0% packet loss\n"
            "rtt min/avg/max/mdev = 1.0/2.0/3.0/0.5 ms"
        )
    )
    assert latency == 2.0
    assert loss == 0.0


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

    def fake_getaddrinfo(host: str, port: int, type: int = 0) -> list[tuple[Any, ...]]:
        return [(checks.socket.AF_INET, checks.socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    class DummyFile:
        def readline(self, _: int) -> bytes:
            return b"HTTP/1.1 204 No Content\r\n"

        def close(self) -> None:
            return None

    class DummySocket:
        def settimeout(self, _: float) -> None:
            return None

        def connect(self, _: Any) -> None:
            return None

        def sendall(self, _: bytes) -> None:
            return None

        def makefile(self, _: str) -> DummyFile:
            return DummyFile()

        def close(self) -> None:
            return None

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
    monkeypatch.setattr(checks.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(checks.socket, "socket", lambda *args, **kwargs: DummySocket())

    result = checks.run_checks(
        _target(
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
        _target(
            network_probe_enabled=True,
            network_interface="wlan0",
            http_probe_target=None,
        )
    )
    obs = result.observations
    assert obs["default_route_ok"] is False
    assert obs["gateway_ok"] is None
    assert obs["internet_ip_ok"] is False


def test_stats_checks_handles_none_payload(monkeypatch: Any) -> None:
    monkeypatch.setattr(checks, "_load_stats", lambda path: (None, None))
    failures: list[checks.CheckFailure] = []
    obs: dict[str, Any] = {}
    checks._stats_checks(
        target=_target(stats_file=Path("/tmp/unused.json"), stats_updated_max_age_sec=10),
        failures=failures,
        observations=obs,
        now_wall_ts=1_000_000.0,
    )
    assert failures == []


def test_apply_records_progress_check_ignores_missing_records() -> None:
    from raspi_sentinel.state_models import TargetState

    state = TargetState.from_dict(
        {
            "last_records_processed_total": 5,
            "records_stalled_cycles": 2,
            "clock_prev_wall_time_epoch": 1234.5,
        }
    )
    result = checks.CheckResult(target="demo", healthy=True, failures=[], observations={})

    checks.apply_records_progress_check(
        target=_target(stats_records_stall_cycles=2),
        target_state=state,
        result=result,
    )

    assert result.failures == []
    assert result.healthy
    assert state.last_records_processed_total == 5
    assert state.records_stalled_cycles == 2
    assert state.clock_prev_wall_time_epoch == 1234.5


def test_apply_records_progress_check_detects_stall_and_preserves_extra_state() -> None:
    from raspi_sentinel.state_models import TargetState

    state = TargetState.from_dict(
        {
            "last_records_processed_total": 10,
            "records_stalled_cycles": 1,
            "clock_prev_wall_time_epoch": 2000.0,
        }
    )
    result = checks.CheckResult(
        target="demo",
        healthy=True,
        failures=[],
        observations={"records_processed_total": 10},
    )

    checks.apply_records_progress_check(
        target=_target(stats_records_stall_cycles=2),
        target_state=state,
        result=result,
    )

    assert state.last_records_processed_total == 10
    assert state.records_stalled_cycles == 2
    assert state.clock_prev_wall_time_epoch == 2000.0
    assert any(f.check == "semantic_records_stalled" for f in result.failures)
    assert not result.healthy


def test_apply_records_progress_check_resets_on_counter_drop() -> None:
    from raspi_sentinel.state_models import TargetState

    state = TargetState.from_dict(
        {
            "last_records_processed_total": 10,
            "records_stalled_cycles": 4,
            "clock_prev_monotonic_sec": 333.3,
        }
    )
    result = checks.CheckResult(
        target="demo",
        healthy=True,
        failures=[],
        observations={"records_processed_total": 7},
    )

    checks.apply_records_progress_check(
        target=_target(stats_records_stall_cycles=3),
        target_state=state,
        result=result,
    )

    assert state.last_records_processed_total == 7
    assert state.records_stalled_cycles == 0
    assert state.clock_prev_monotonic_sec == 333.3
    assert result.failures == []
    assert result.healthy
