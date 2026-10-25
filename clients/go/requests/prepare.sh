#!/usr/bin/env bash
set -euo pipefail
cd clients/go/requests
go mod tidy
go build -o fpdb-requests .
