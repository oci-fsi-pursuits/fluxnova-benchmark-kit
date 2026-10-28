#!/bin/sh
set -eu
cd /opt/fluxnova
exec java ${JAVA_OPTS:--Xms4g -Xmx4g} \
  -Dloader.path=internal/rest,internal/webapps,configuration/userlib \
  -jar internal/fluxnova-bpm-run-core.jar \
  --spring.config.location=file:configuration/default.yml \
  --fluxnova.bpm.run.example.enabled=false \
  --fluxnova.bpm.run.cors.enabled=false
