#!/usr/bin/env bash
set -euo pipefail
cd clients/go/retryablehttp
go mod tidy
go build -o fpdb-retryablehttp .
