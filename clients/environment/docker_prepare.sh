#!/usr/bin/env bash
set -euo pipefail
variant="$1"
case "$variant" in
  debian13) docker build -q -t fpdb-env-debian13 --build-arg BASE_IMAGE=debian:13 -f clients/environment/Dockerfile.debian clients/environment ;;
  ubuntu2604) docker build -q -t fpdb-env-ubuntu2604 --build-arg BASE_IMAGE=ubuntu:26.04 -f clients/environment/Dockerfile.debian clients/environment ;;
  alpine322) docker build -q -t fpdb-env-alpine322 -f clients/environment/Dockerfile.alpine clients/environment ;;
  openssl36|libressl283) docker build -q -t "fpdb-env-$variant" -f "clients/environment/Dockerfile.$variant" clients/environment ;;
  *) echo 'unsupported image' >&2; exit 2 ;;
esac
