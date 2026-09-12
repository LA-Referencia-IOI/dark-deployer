#!/bin/sh
set -eu

# The Besu image initializes /data itself. Create the optional Bloom-cache
# directory from inside that same container so bind mounts behave identically
# on local Docker, Lima and production hosts.
mkdir -p /data/caches
test -w /data/caches

exec besu "$@"
