#!/usr/bin/env bash
set -euo pipefail
mode="$1"; endpoint="$2"
case "$mode" in
  system) python=/usr/bin/python3 ;;
  homebrew) python="$(brew --prefix python@3.14)/bin/python3.14" ;;
  *) exit 2 ;;
esac
export PYTHONPATH="${RUNNER_TEMP:?}/fpdb-${mode}-requests"
"$python" clients/environment/probe.py requests "$endpoint"
