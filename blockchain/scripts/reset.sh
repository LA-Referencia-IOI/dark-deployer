#!/usr/bin/env bash
# =============================================================================
#  dARK blockchain runtime — scripts/reset.sh
#  Tears down all containers and wipes node data for a clean restart.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.."

RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m'

echo -e "${RED}============================================================${NC}"
echo -e "${RED}  WARNING: This will DELETE all blockchain data!${NC}"
echo -e "${RED}============================================================${NC}"
echo ""
read -r -p "Are you sure? Type 'yes' to confirm: " confirm

if [ "$confirm" != "yes" ]; then
  echo "Cancelled."
  exit 0
fi

echo ""
echo -e "${YELLOW}Stopping and removing containers...${NC}"
cd "$SCRIPT_DIR"
for project in dark-apps dark-bc-a dark-bc-b; do
  docker compose --project-directory "$SCRIPT_DIR" --project-name "$project" down -v --remove-orphans 2>/dev/null || true
done

echo -e "${YELLOW}Wiping node data...${NC}"
for node in nodes/validator01 nodes/validator02 nodes/validator03 nodes/validator04 nodes/rpc01; do
  if [ -d "$SCRIPT_DIR/$node/data" ]; then
    rm -rf "${SCRIPT_DIR:?}/$node/data"
    mkdir -p "$SCRIPT_DIR/$node/data"
    echo "  Cleared: $node/data"
  fi
done

echo -e "${YELLOW}Removing generated genesis and static-nodes files...${NC}"
rm -f "$SCRIPT_DIR/config/genesis.json"
rm -f "$SCRIPT_DIR/config/static-nodes.json"
# Keep config/master-wallet together with master-wallet.txt.  The former is the
# public address consumed by setup.sh; deleting it while retaining the key file
# makes a clean chain reset impossible without manually reconstructing it.
# Delete both files manually only when a completely new platform wallet is wanted.

echo ""
echo -e "${GREEN}Reset complete!${NC}"
echo ""
echo "To reconfigure the network:"
echo "  ./setup.sh"
echo "  ./scripts/start-role.sh all"
