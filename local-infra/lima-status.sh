#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "--store" && -n "${2:-}" ]] || { echo "Usage: $0 --store PATH" >&2; exit 2; }
STORE="$2"
STORE="$(cd "$(dirname "$STORE")" && pwd)/$(basename "$STORE")"
export LIMA_HOME="$STORE/.lima"
limactl list
for name in dark-apps dark-blockchain-a dark-blockchain-b dark-storage-1 dark-storage-2; do
  echo "--- $name ---"
  limactl shell "$name" docker version --format '{{.Server.Version}}' 2>/dev/null || true
done
