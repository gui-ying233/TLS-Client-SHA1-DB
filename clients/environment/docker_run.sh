#!/usr/bin/env bash
set -euo pipefail
variant="$1"; kind="$2"; endpoint="$3"
case "$variant" in debian13|ubuntu2604|alpine322|openssl36|libressl283) ;; *) exit 2;; esac
# Share the runner loopback and explicitly trust only the ephemeral local CA.
[[ -r "${FPDB_LOCAL_CA:?Missing local CA}" ]]
docker run --rm --network host --add-host echo.fpdb.test:127.0.0.1 \
  -v "$FPDB_LOCAL_CA:/tmp/fpdb-ca.crt:ro" -e FPDB_LOCAL_CA=/tmp/fpdb-ca.crt \
  --pull=never "fpdb-env-$variant" python3 /fpdb-probe.py "$kind" "$endpoint"
