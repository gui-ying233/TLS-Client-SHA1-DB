#!/usr/bin/env bash
set -euo pipefail
prefix="${RUNNER_TEMP:-/tmp}/fpdb-openssl-3.5.5"
if [[ -x "$prefix/bin/openssl" ]] && [[ "$("$prefix/bin/openssl" version)" == "OpenSSL 3.5.5"* ]]; then exit 0; fi
mkdir -p "$prefix"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
url=https://github.com/openssl/openssl/releases/download/openssl-3.5.5/openssl-3.5.5.tar.gz
curl -fsSL --retry 3 "$url" -o "$work/openssl-3.5.5.tar.gz"
curl -fsSL --retry 3 "$url.sha256" -o "$work/openssl-3.5.5.tar.gz.sha256"
(cd "$work" && sha256sum -c openssl-3.5.5.tar.gz.sha256)
tar -xzf "$work/openssl-3.5.5.tar.gz" -C "$work"
cd "$work/openssl-3.5.5"
./Configure --prefix="$prefix" --openssldir="$prefix/ssl" no-tests no-shared
make -j"$(nproc)"
make install_sw
"$prefix/bin/openssl" version | grep -F 'OpenSSL 3.5.5'
