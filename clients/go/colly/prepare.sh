#!/usr/bin/env bash
set -euo pipefail
cd clients/go/colly
go mod tidy
go build -o fpdb-colly .
