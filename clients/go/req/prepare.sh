#!/usr/bin/env bash
set -euo pipefail
cd clients/go/req
go mod tidy
go build -o fpdb-req .
