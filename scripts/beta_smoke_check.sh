#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

cd "${ROOT_DIR}"

"${PYTHON_BIN}" -m ruff check src tests
"${PYTHON_BIN}" -m ruff format --check src tests
"${PYTHON_BIN}" -m mypy src

PYTHONPATH=src "${PYTHON_BIN}" -m pytest -q \
  tests/unit/test_systemd_units.py \
  tests/unit/test_install_systemd.py \
  tests/unit/test_config_validation.py \
  tests/unit/test_config_state.py \
  tests/unit/test_config_summary.py \
  tests/unit/test_checks_internal_file_command.py \
  tests/scenario/test_engine_integration.py

cat > "${TMP_DIR}/config.toml" <<EOF
[global]
state_file = "${TMP_DIR}/state.json"
events_file = "${TMP_DIR}/events.jsonl"
monitor_stats_file = "${TMP_DIR}/stats.json"
monitor_stats_interval_sec = 30
restart_threshold = 99
reboot_threshold = 999
restart_cooldown_sec = 3600
reboot_cooldown_sec = 86400
reboot_window_sec = 21600
max_reboots_in_window = 2
min_uptime_for_reboot_sec = 600
default_command_timeout_sec = 10
restart_service_timeout_sec = 30
loop_interval_sec = 30

[notify.discord]
enabled = false
username = "raspi-sentinel"
timeout_sec = 5
followup_delay_sec = 300
retry_interval_sec = 60
retry_backoff_base_sec = 0.5
heartbeat_interval_sec = 0
notify_on_recovery = false

[[targets]]
name = "beta_demo_heartbeat"
services = []
service_active = false
heartbeat_file = "${TMP_DIR}/heartbeat.txt"
heartbeat_max_age_sec = 60
restart_threshold = 99
reboot_threshold = 999
EOF

PYTHONPATH=src "${PYTHON_BIN}" scripts/failure_inject.py fresh-file --path "${TMP_DIR}/heartbeat.txt"

PYTHONPATH=src "${PYTHON_BIN}" -m raspi_sentinel.cli \
  -c "${TMP_DIR}/config.toml" \
  validate-config --strict

PYTHONPATH=src "${PYTHON_BIN}" -m raspi_sentinel.cli \
  -c "${TMP_DIR}/config.toml" \
  --dry-run run-once --json >/dev/null

BIN_PATH="$(command -v raspi-sentinel || true)"
if [[ -n "${BIN_PATH}" && "${BIN_PATH}" != /* ]]; then
  BIN_PATH="$(cd "$(dirname "${BIN_PATH}")" && pwd)/$(basename "${BIN_PATH}")"
fi
if [[ -n "${BIN_PATH}" && "${BIN_PATH}" == /home/* ]]; then
  BIN_PATH="/usr/bin/raspi-sentinel"
  echo "warning: detected /home-based raspi-sentinel path; using ${BIN_PATH} for systemd render smoke check"
fi
if [[ -z "${BIN_PATH}" ]]; then
  BIN_PATH="/usr/bin/raspi-sentinel"
  echo "warning: raspi-sentinel binary not found in PATH, using ${BIN_PATH} for render smoke check"
fi

"${PYTHON_BIN}" scripts/install_systemd.py \
  --dry-run \
  --raspi-sentinel-bin "${BIN_PATH}"
