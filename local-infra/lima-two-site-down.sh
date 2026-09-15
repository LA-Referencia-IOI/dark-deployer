#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "--store" && -n "${2:-}" ]] || { echo "Usage: $0 --store PATH" >&2; exit 2; }
STORE="$2"
STORE="$(cd "$(dirname "$STORE")" && pwd)/$(basename "$STORE")"
export LIMA_HOME="$STORE/.lima"
for name in dark-two-site-apps dark-two-site-blockchain-a dark-two-site-storage-1 dark-two-site-blockchain-b dark-two-site-storage-2; do
  limactl stop "$name" 2>/dev/null || true
done
