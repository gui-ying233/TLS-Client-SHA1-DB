#!/usr/bin/env bash
# Run actual fingerprints on your Docker host; never call the GitHub Actions API.
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
command -v docker >/dev/null || { echo 'Docker is required' >&2; exit 2; }
cache="${FPDB_DOCKER_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/fpdb-local}"
mkdir -p "$cache"
image="${FPDB_DOCKER_IMAGE:-fpdb-local:ubuntu-24.04}"
if [[ "${FPDB_DOCKER_SKIP_BUILD:-0}" != 1 ]]; then
  docker build --platform linux/amd64 -t "$image" - < "$repo/Dockerfile.local"
fi
extra=()
if [[ -n "${GITHUB_TOKEN:-}" ]]; then extra+=(-e GITHUB_TOKEN); fi
docker run --rm --init --platform linux/amd64 \
  --add-host echo.fpdb.test:127.0.0.1 \
  -v "$repo:/repo" -v "$cache:/cache" \
  --workdir /repo \
  -e FPDB_UID="$(id -u)" -e FPDB_GID="$(id -g)" \
  "${extra[@]}" "$image" "$@"
