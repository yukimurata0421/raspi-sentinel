#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

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

PYTHONPATH=src "${PYTHON_BIN}" -m raspi_sentinel.cli \
  -c config/raspi-sentinel.beta-demo.toml \
  validate-config --strict

PYTHONPATH=src "${PYTHON_BIN}" -m raspi_sentinel.cli \
  -c config/raspi-sentinel.beta-demo.toml \
  --dry-run run-once --json >/dev/null

BIN_PATH="$(command -v raspi-sentinel || true)"
if [[ -n "${BIN_PATH}" && "${BIN_PATH}" != /* ]]; then
  BIN_PATH="$(cd "$(dirname "${BIN_PATH}")" && pwd)/$(basename "${BIN_PATH}")"
fi
if [[ -z "${BIN_PATH}" ]]; then
  BIN_PATH="/usr/bin/raspi-sentinel"
  echo "warning: raspi-sentinel binary not found in PATH, using ${BIN_PATH} for render smoke check"
fi

"${PYTHON_BIN}" scripts/install_systemd.py \
  --dry-run \
  --raspi-sentinel-bin "${BIN_PATH}"
