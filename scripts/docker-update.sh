#!/usr/bin/env bash
# Run actual fingerprints on your Docker host; never call the GitHub Actions API.
export FPDB_DOCKER_SKIP_BUILD=1
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
# Git Bash (MSYS) rewrites Linux container paths such as /repo to
# C:/Program Files/Git/repo. Pass native Windows bind paths and disable
# MSYS argument conversion for this one Docker invocation.
if command -v cygpath >/dev/null 2>&1; then
  repo_host="$(cygpath -m "$repo")"
  cache_host="$(cygpath -m "$cache")"
  # Docker Desktop Windows bind mounts are not governed by POSIX uid/gid.
  host_uid=0
  host_gid=0
else
  repo_host="$repo"
  cache_host="$cache"
  host_uid="$(id -u)"
  host_gid="$(id -g)"
fi
# A Windows checkout can convert bind-mounted .sh files to CRLF. Keep an
# LF-only copy of the entrypoint in the cache; do not modify the checkout.
tr -d '\r' < "$repo/scripts/docker-entrypoint.sh" > "$cache/docker-entrypoint.sh"
MSYS_NO_PATHCONV=1 docker run --rm --init --platform linux/amd64 \
  --add-host echo.fpdb.test:127.0.0.1 \
  --mount "type=bind,source=$repo_host,target=/repo" \
  --mount "type=bind,source=$cache_host,target=/cache" \
  --workdir /repo --entrypoint /bin/bash \
  -e "FPDB_UID=$host_uid" -e "FPDB_GID=$host_gid" \
  "${extra[@]}" "$image" /cache/docker-entrypoint.sh "$@"
