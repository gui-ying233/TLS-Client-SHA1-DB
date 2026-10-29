#!/usr/bin/env bash
set -euo pipefail
variant="$1"; kind="$2"; endpoint="$3"
case "$variant" in debian13|ubuntu2604|alpine322|openssl36|libressl283) ;; *) exit 2;; esac
# Share the runner loopback. Only the default mode needs the local CA.
ca_args=()
if [[ "${FPDB_TLS_VERIFY:-default}" != false ]]; then
  [[ -r "${FPDB_LOCAL_CA:?Missing local CA}" ]]
  ca_args=(-v "$FPDB_LOCAL_CA:/tmp/fpdb-ca.crt:ro" -e FPDB_LOCAL_CA=/tmp/fpdb-ca.crt)
fi
docker run --rm --network host --add-host echo.fpdb.test:127.0.0.1 \
  "${ca_args[@]}" \
  -e "FPDB_TLS_VERIFY=${FPDB_TLS_VERIFY:-default}" \
  --pull=never "fpdb-env-$variant" python3 /fpdb-probe.py "$kind" "$endpoint"
