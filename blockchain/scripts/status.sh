#!/usr/bin/env bash
# =============================================================================
#  dARK blockchain runtime — scripts/status.sh
#  Displays a quick network health summary.
# =============================================================================

set -euo pipefail

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -f "$ROOT_DIR/.env.runtime" ]; then
  set -a
  source "$ROOT_DIR/.env.runtime"
  set +a
fi
ROLE="${BLOCKCHAIN_RUNTIME_ROLE:-all}"
RPC_URL="http://${BLOCKCHAIN_BIND_ADDRESS:-127.0.0.1}:8545"

echo ""
echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN}  dARK blockchain runtime — Network Status${NC}"
echo -e "${CYAN}============================================================${NC}"
echo ""

# Container status
echo -e "${YELLOW}Containers:${NC}"
case "$ROLE" in
  rpc) docker compose --project-directory "$ROOT_DIR" --project-name dark-apps --profile rpc ps ;;
  validators-a) docker compose --project-directory "$ROOT_DIR" --project-name dark-bc-a --profile validators-a ps ;;
  validators-b) docker compose --project-directory "$ROOT_DIR" --project-name dark-bc-b --profile validators-b ps ;;
  all)
    docker compose --project-directory "$ROOT_DIR" --project-name dark-bc-a --profile validators-a ps
    docker compose --project-directory "$ROOT_DIR" --project-name dark-bc-b --profile validators-b ps
    docker compose --project-directory "$ROOT_DIR" --project-name dark-apps --profile rpc ps
    ;;
esac
echo ""

# Blockchain status via RPC
echo -e "${YELLOW}Blockchain RPC (${RPC_URL}):${NC}"

BLOCK_HEX=$(curl -s -X POST "$RPC_URL" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' 2>/dev/null \
  | grep -o '"result":"[^"]*"' | cut -d'"' -f4 || echo "")

if [ -z "$BLOCK_HEX" ]; then
  echo -e "  ${RED}RPC not reachable at $RPC_URL${NC}"
else
  BLOCK_DEC=$((16#${BLOCK_HEX#0x}))
  echo -e "  Current block : ${GREEN}#$BLOCK_DEC${NC}"

  PEERS=$(curl -s -X POST "$RPC_URL" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"net_peerCount","params":[],"id":1}' 2>/dev/null \
    | grep -o '"result":"[^"]*"' | cut -d'"' -f4 || echo "0x0")
  PEERS_DEC=$((16#${PEERS#0x}))
  echo -e "  Peers         : ${GREEN}$PEERS_DEC${NC}"

  CHAIN=$(curl -s -X POST "$RPC_URL" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}' 2>/dev/null \
    | grep -o '"result":"[^"]*"' | cut -d'"' -f4 || echo "0x0")
  CHAIN_DEC=$((16#${CHAIN#0x}))
  echo -e "  Chain ID      : ${GREEN}$CHAIN_DEC${NC}"
fi

echo ""
echo ""
