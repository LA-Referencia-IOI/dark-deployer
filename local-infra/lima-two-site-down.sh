#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "--store" && -n "${2:-}" ]] || { echo "Usage: $0 --store PATH" >&2; exit 2; }
STORE="$2"
STORE="$(cd "$(dirname "$STORE")" && pwd)/$(basename "$STORE")"
export LIMA_HOME="$STORE/.lima"
instances=()
for instance_dir in "$LIMA_HOME"/*; do
  [[ -d "$instance_dir" && -f "$instance_dir/lima.yaml" ]] || continue
  instances+=("${instance_dir##*/}")
done

if ((${#instances[@]} == 0)); then
  echo "[info] no hay instancias Lima en $LIMA_HOME"
  exit 0
fi

failed=0
for name in "${instances[@]}"; do
  echo "[stop] $name"
  if ! limactl stop "$name"; then
    echo "[retry] $name con parada forzada"
    if ! limactl stop -f "$name"; then
      echo "[error] no se pudo detener $name" >&2
      failed=1
    fi
  fi
done

exit "$failed"
