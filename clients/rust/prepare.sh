#!/usr/bin/env bash
set -euo pipefail
version="$1"; client="$2"
rustup toolchain install "$version" --profile minimal
feature='rustls'
if [[ "$client" == 'reqwest-native' ]]; then feature='native'; fi
cd clients/rust
export FPDB_RUSTC_VERSION="$(rustup run "$version" rustc --version | awk '{print $2}')"
cargo "+$version" build --release --no-default-features --features "$feature"
