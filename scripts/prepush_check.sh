#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"
export PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"

PYTHON_BIN="python3"
if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
fi

"${PYTHON_BIN}" -m ruff check src tests
"${PYTHON_BIN}" -m ruff format --check src tests
"${PYTHON_BIN}" -m mypy
"${PYTHON_BIN}" -m pytest -q \
  tests/unit/test_public_secret_scan.py \
  tests/unit/test_example_config_placeholders.py
