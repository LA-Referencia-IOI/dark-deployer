#!/usr/bin/env bash
# Install a role-specific chain artifact provisioned out of band with mode 0600.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROLE="${1:?usage: import-role-artifact.sh <rpc|validators-a|validators-b> <artifact-dir>}"
ARTIFACT_DIR="${2:?usage: import-role-artifact.sh <role> <artifact-dir>}"

case "$ROLE" in
  rpc) nodes=(rpc01) ;;
  validators-a) nodes=(validator01 validator02) ;;
  validators-b) nodes=(validator03 validator04) ;;
  *) echo "invalid role: $ROLE" >&2; exit 2 ;;
esac

test -f "$ARTIFACT_DIR/config/genesis.json" || { echo "artifact genesis.json is missing" >&2; exit 1; }
mkdir -p "$ROOT_DIR/config"
install -m 0644 "$ARTIFACT_DIR/config/genesis.json" "$ROOT_DIR/config/genesis.json"
for node in "${nodes[@]}"; do
  source_dir="$ARTIFACT_DIR/nodes/$node/data"
  test -f "$source_dir/nodekey" || { echo "artifact node key missing for $node" >&2; exit 1; }
  node_dir="${BLOCKCHAIN_DATA_ROOT:-$ROOT_DIR/nodes}/$node"
  mkdir -p "$node_dir"
  install -m 0600 "$source_dir/nodekey" "$node_dir/nodekey"
  install -m 0644 "$source_dir/nodekey.pub" "$node_dir/nodekey.pub"
  install -m 0644 "$source_dir/static-nodes.json" "$node_dir/static-nodes.json"
done
echo "Imported $ROLE chain artifact"
