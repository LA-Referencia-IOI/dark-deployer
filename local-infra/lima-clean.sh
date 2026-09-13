#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 --store PATH [--yes]" >&2
  exit 2
}

[[ "${1:-}" == "--store" && -n "${2:-}" ]] || usage
STORE="$2"
shift 2
YES=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes) YES=true; shift;;
    *) usage;;
  esac
done

STORE="$(cd "$(dirname "$STORE")" && pwd)/$(basename "$STORE")"
export LIMA_HOME="$STORE/.lima"
INSTANCES=(dark-apps dark-blockchain-a dark-blockchain-b dark-storage-1 dark-storage-2 dark-lab-base)

if [[ ! -d "$LIMA_HOME" ]]; then
  echo "[ok] No Lima store found at $LIMA_HOME"
  exit 0
fi

if [[ "$YES" != true ]]; then
  echo "This will permanently delete these Lima instances from: $LIMA_HOME"
  printf '  %s\n' "${INSTANCES[@]}"
  read -r -p "Continue? [y/N] " answer
  [[ "$answer" =~ ^[Yy]([Ee][Ss])?$ ]] || { echo "[cancelled]"; exit 0; }
fi

limactl delete --yes --force "${INSTANCES[@]}"
echo "[ok] Lima lab instances removed from $LIMA_HOME"
