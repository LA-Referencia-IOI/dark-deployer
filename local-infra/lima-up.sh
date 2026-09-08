#!/usr/bin/env bash
set -euo pipefail

usage() { echo "Usage: $0 --store PATH [--cpus N] [--memory GiB] [--disk GiB]" >&2; exit 2; }
STORE=""
CPUS=2
MEMORY=4
DISK=30
while [[ $# -gt 0 ]]; do
  case "$1" in
    --store) [[ $# -ge 2 ]] || usage; STORE="$2"; shift 2;;
    --cpus) CPUS="$2"; shift 2;;
    --memory) MEMORY="$2"; shift 2;;
    --disk) DISK="$2"; shift 2;;
    -h|--help) usage;;
    *) usage;;
  esac
done
[[ -n "$STORE" ]] || usage
command -v limactl >/dev/null || { echo "limactl no está instalado (brew install lima)" >&2; exit 1; }
STORE="$(cd "$(dirname "$STORE")" && pwd)/$(basename "$STORE")"
mkdir -p "$STORE"
export LIMA_HOME="$STORE/.lima"

for name in dark-blockchain dark-apps dark-storage-a dark-storage-b; do
  if limactl list --format '{{.Name}}' | grep -Fxq "$name"; then
    echo "[skip] $name ya existe en $LIMA_HOME"
  else
    echo "[create] $name"
    limactl start --name="$name" --cpus="$CPUS" --memory="${MEMORY}GiB" --disk="${DISK}GiB" --network=lima:shared template:docker
  fi
done

echo "[ok] Lima store: $LIMA_HOME"
limactl list
