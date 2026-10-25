#!/usr/bin/env bash
set -euo pipefail
sudo apt-get update -qq
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3-requests curl ca-certificates
