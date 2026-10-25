#!/usr/bin/env bash
set -euo pipefail
variant="$1"; kind="$2"; endpoint="$3"
case "$variant" in debian13|ubuntu2604|alpine322|openssl36|libressl283) ;; *) exit 2;; esac
docker run --rm --network bridge --pull=never "fpdb-env-$variant" python3 /fpdb-probe.py "$kind" "$endpoint"
