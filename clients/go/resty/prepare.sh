#!/usr/bin/env bash
set -euo pipefail
cd clients/go/resty
go mod tidy
go build -o fpdb-resty .
