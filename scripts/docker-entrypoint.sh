#!/usr/bin/env sh
# Minimal entrypoint for g2nc container
set -eu

# Ensure /data exists (mounted volume expected)
mkdir -p /data

# Default to JSON logs in containers unless explicitly disabled
: "${G2NC_FORCE_JSON_LOGS:=1}"
export G2NC_FORCE_JSON_LOGS

# If the first arg looks like an option, prepend the default command
if [ "${1:-}" = "" ] || [ "${1#-}" != "$1" ]; then
  set -- g2nc sync --config /data/config.yaml "$@"
fi

exec "$@"