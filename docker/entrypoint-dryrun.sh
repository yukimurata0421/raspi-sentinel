#!/usr/bin/env sh
set -eu

DEFAULT_CONFIG="/config/config.toml"

if [ "$#" -eq 0 ]; then
  set -- -c "$DEFAULT_CONFIG" --dry-run run-once --json
fi

has_run_once=false
has_dry_run=false

for arg in "$@"; do
  case "$arg" in
    loop|verify-storage)
      echo "docker dry-run image supports only: run-once --dry-run" >&2
      exit 2
      ;;
    run-once)
      has_run_once=true
      ;;
    --dry-run)
      has_dry_run=true
      ;;
  esac
done

if [ "$has_run_once" = false ]; then
  echo "docker dry-run image requires command: run-once" >&2
  exit 2
fi

if [ "$has_dry_run" = false ]; then
  set -- --dry-run "$@"
fi

exec raspi-sentinel "$@"
