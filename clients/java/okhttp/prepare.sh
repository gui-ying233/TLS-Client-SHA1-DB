#!/usr/bin/env bash
set -euo pipefail
mvn -B -q -f clients/java/okhttp/pom.xml dependency:build-classpath -Dmdep.outputFile=classpath.txt
javac -cp "$(cat clients/java/okhttp/classpath.txt)" -d clients/java/okhttp clients/java/okhttp/ProbeOkHttp.java
