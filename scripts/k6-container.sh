#!/usr/bin/env bash
# Execute the pinned Linux k6 image on a separate load-generator host.
set -euo pipefail
case "${BENCH_K6_IMAGE:-}" in
  *@sha256:????????????????????????????????????????????????????????????????) ;;
  *) echo 'BENCH_K6_IMAGE must be pinned to a registry SHA-256 digest' >&2; exit 2 ;;
esac
case "${BENCH_OUTPUT_DIR:-$PWD}" in
  "$PWD"|"$PWD"/*) ;;
  *) echo 'Container k6 output must be within the current benchmark workspace' >&2; exit 2 ;;
esac
env_args=()
while IFS= read -r name; do
  case "$name" in BENCH_*|SSL_CERT_FILE) env_args+=(--env "$name") ;; esac
done < <(compgen -e)
exec docker run --rm --network host --user "$(id -u):$(id -g)" \
  --volume "$PWD:$PWD" --workdir "$PWD" "${env_args[@]}" "$BENCH_K6_IMAGE" "$@"
