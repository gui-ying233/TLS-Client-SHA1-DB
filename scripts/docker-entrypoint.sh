#!/usr/bin/env bash
set -euo pipefail
: "${FPDB_UID:?Missing host uid}"
: "${FPDB_GID:?Missing host gid}"
[[ "$FPDB_UID" =~ ^[0-9]+$ && "$FPDB_GID" =~ ^[0-9]+$ ]] || exit 2
# Do all CA/hosts changes strictly inside this disposable container.
certdir="$(mktemp -d /tmp/fpdb-echo.XXXXXX)"
python3 -m fpdb.local_echo setup "$certdir" --java
chmod 755 "$certdir"       # CA cert may be read by the unprivileged probe user.
export FPDB_LOCAL_CA="$certdir/ca.crt"
python3 -m fpdb.local_echo serve "$certdir" > /tmp/fpdb-echo.log 2>&1 &
echo_pid=$!
# Use a TLS handshake, not a bare TCP connect, to test readiness.
ready=0
for _ in $(seq 1 30); do
  if curl -fsS --noproxy '*' --cacert "$FPDB_LOCAL_CA" --max-time 2 \
      https://echo.fpdb.test:18443/ >/dev/null 2>&1; then ready=1; break; fi
  if ! kill -0 "$echo_pid" 2>/dev/null; then break; fi
  sleep 0.2
done
if [[ "$ready" != 1 ]]; then
  cat /tmp/fpdb-echo.log >&2
  echo 'Local TLS echo failed to start' >&2
  exit 1
fi
# The host checkout and caches remain owned by the invoking user.
install -d -o "$FPDB_UID" -g "$FPDB_GID" /repo/.fpdb-local /cache/build \
  /cache/home /cache/node /cache/lua /cache/uv-cache /cache/uv-python /cache/runner-temp \
  /cache/mise /cache/runtimes /cache/mise-cache /cache/mise-config /cache/mise-state \
  /cache/cargo /cache/rustup /cache/dotnet /cache/gems /cache/php /cache/go-build /cache/go-mod /cache/cargo-target /cache/version-metadata
export HOME=/cache/home
export UV_CACHE_DIR=/cache/uv-cache UV_PYTHON_INSTALL_DIR=/cache/uv-python
export RUNNER_TEMP=/cache/runner-temp
export FPDB_BUILD_ROOT=/cache/build
export FPDB_LUA_CACHE_ROOT=/cache/lua
export FPDB_VERSION_CACHE_DIR=/cache/version-metadata
export FPDB_CAPTURE_PROFILE="local-docker-ubuntu2404-v1"
# Do not set SSL_CERT_FILE/CURL_CA_BUNDLE globally: package downloads from
# nodejs.org/PyPI/GitHub still need the ordinary public CA store. The existing
# collector applies the temporary CA only to the individual TLS probes.
export NODE_EXTRA_CA_CERTS="$FPDB_LOCAL_CA"
export PYTHONPATH=/repo
exec setpriv --reuid "$FPDB_UID" --regid "$FPDB_GID" --clear-groups \
  python3 scripts/docker-runner.py "$@"
