#!/bin/sh
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run as root.' >&2; exit 1; }
release=$(realpath "$1")
config=$(realpath "$2")
case "$release" in /opt/fluxnova-bound-verifier/releases/*) ;; *) echo 'Unexpected release path.' >&2; exit 1;; esac
[ -f "$release/services/verifier_service.py" ] && [ -f "$config" ]
if ! id fluxnova-verifier >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/fluxnova-bound-verifier --shell /usr/sbin/nologin fluxnova-verifier
fi
install -d -m 0700 -o fluxnova-verifier -g fluxnova-verifier /var/lib/fluxnova-bound-verifier
install -d -m 0755 /etc/fluxnova-bound-verifier
if [ "$config" != /etc/fluxnova-bound-verifier/config.json ]; then
  install -m 0644 "$config" /etc/fluxnova-bound-verifier/config.json
else
  chmod 0644 "$config"
fi
runuser -u fluxnova-verifier -- /opt/fluxnova-bench/venv/bin/python -c 'import oci'
current=/opt/fluxnova-bound-verifier/current
if [ -e "$current" ] && [ ! -L "$current" ]; then echo 'Current release is not a symlink.' >&2; exit 1; fi
ln -sfn "$release" "$current"
install -m 0644 "$release/deploy/fluxnova-bound-verifier.service" /etc/systemd/system/fluxnova-bound-verifier.service
systemctl daemon-reload
systemctl enable fluxnova-bound-verifier.service
# Persist the release, config, unit and enablement links before a worker can
# encounter power loss. The hard-stop rehearsal exposed an empty unit file
# after an installation whose writes had not been flushed.
sync
systemctl restart fluxnova-bound-verifier.service
