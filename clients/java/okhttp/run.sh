#!/usr/bin/env bash
set -euo pipefail
exec java -cp "clients/java/okhttp:$(cat clients/java/okhttp/classpath.txt)" ProbeOkHttp "$@"
