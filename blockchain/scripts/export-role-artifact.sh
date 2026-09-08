#!/usr/bin/env bash
# Export only the material a single host needs after a greenfield all-role setup.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROLE="${1:?usage: export-role-artifact.sh <rpc|validators-a|validators-b> <destination>}"
DESTINATION="${2:?usage: export-role-artifact.sh <role> <destination>}"

case "$ROLE" in
  rpc) nodes=(rpc01) ;;
  validators-a) nodes=(validator01 validator02) ;;
  validators-b) nodes=(validator03 validator04) ;;
  *) echo "invalid role: $ROLE" >&2; exit 2 ;;
esac

test -f "$ROOT_DIR/config/genesis.json" || { echo "genesis.json is missing; run ./setup.sh on the initializer first" >&2; exit 1; }
mkdir -p "$DESTINATION/config" "$DESTINATION/nodes"
install -m 0644 "$ROOT_DIR/config/genesis.json" "$DESTINATION/config/genesis.json"
for node in "${nodes[@]}"; do
  source_dir="$ROOT_DIR/nodes/$node/data"
  test -f "$source_dir/nodekey" || { echo "node key missing for $node" >&2; exit 1; }
  mkdir -p "$DESTINATION/nodes/$node/data"
  install -m 0600 "$source_dir/nodekey" "$DESTINATION/nodes/$node/data/nodekey"
  install -m 0644 "$source_dir/nodekey.pub" "$DESTINATION/nodes/$node/data/nodekey.pub"
  install -m 0644 "$source_dir/static-nodes.json" "$DESTINATION/nodes/$node/data/static-nodes.json"
done
echo "Exported $ROLE chain artifact to $DESTINATION"
