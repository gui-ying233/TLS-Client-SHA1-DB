#!/usr/bin/env bash
set -euo pipefail
g++ -std=c++17 -O2 -Wall -Wextra clients/cpp/probe.cpp -o clients/cpp/fpdb-cpp -lssl -lcrypto -pthread
