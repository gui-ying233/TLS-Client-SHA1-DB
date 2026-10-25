#!/usr/bin/env bash
set -euo pipefail
mode="$1"
case "$mode" in
  system) python=/usr/bin/python3 ;;
  homebrew)
    export HOMEBREW_NO_AUTO_UPDATE=1
    brew list python@3.14 >/dev/null 2>&1 || brew install python@3.14
    python="$(brew --prefix python@3.14)/bin/python3.14" ;;
  *) exit 2 ;;
esac
"$python" --version
# Keep application packages outside the system environment; use the Python
# runtime's own pip resolver so Python 3.9 does not get incompatible packages.
root="${RUNNER_TEMP:?}/fpdb-${mode}-requests"
mkdir -p "$root"
if "$python" -m pip --version >/dev/null 2>&1; then
  "$python" -m pip install --disable-pip-version-check --no-input --target "$root" 'requests==2.32.5'
else
  # Some Xcode system Python builds omit pip. These pinned dependencies are
  # Python-3.9-compatible; install via the setup-python control interpreter.
  # Force source distributions for ABI-sensitive packages rather than copying
  # a control-Python binary wheel into the target Python's site-packages.
  python3 -m pip install --disable-pip-version-check --no-input --target "$root" \
    --no-binary=charset-normalizer \
    'requests==2.32.5' 'urllib3==2.3.0' 'charset-normalizer==3.4.0' \
    'idna==3.10' 'certifi==2025.1.31'
fi
