#!/usr/bin/env bash
set -euo pipefail
cd clients/go/gorequest
go mod tidy
go build -o fpdb-gorequest .
