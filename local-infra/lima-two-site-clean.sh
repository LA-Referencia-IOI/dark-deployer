#!/usr/bin/env bash
set -euo pipefail
usage() { echo "Usage: $0 --store PATH [--yes]" >&2; exit 2; }
[[ "${1:-}" == "--store" && -n "${2:-}" ]] || usage
STORE="$2"; shift 2; YES=false
while [[ $# -gt 0 ]]; do
  case "$1" in --yes) YES=true; shift;; *) usage;; esac
done
STORE="$(cd "$(dirname "$STORE")" && pwd)/$(basename "$STORE")"
export LIMA_HOME="$STORE/.lima"
INSTANCES=(dark-two-site-apps dark-two-site-blockchain-a dark-two-site-storage-1 dark-two-site-blockchain-b dark-two-site-storage-2 dark-two-site-base)
[[ -d "$LIMA_HOME" ]] || { echo "[ok] No Lima store found at $LIMA_HOME"; exit 0; }
if [[ "$YES" != true ]]; then
  echo "This permanently deletes only the two-site Lima instances from: $LIMA_HOME"
  printf '  %s\n' "${INSTANCES[@]}"
  read -r -p "Continue? [y/N] " answer
  [[ "$answer" =~ ^[Yy]([Ee][Ss])?$ ]] || { echo "[cancelled]"; exit 0; }
fi
limactl delete --yes --force "${INSTANCES[@]}"
echo "[ok] Two-site Lima instances removed; the original five-host lab was untouched"
