#!/usr/bin/env bash
# =============================================================================
#  dARK blockchain runtime — setup.sh
#  Generates keys, genesis.json and static-nodes.json for the QBFT network.
#  Run ONCE before the first "docker compose up".
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$SCRIPT_DIR/config"

# Host bundles write the non-secret runtime selection here. Developers may
# copy .env.example to .env.runtime.
RUNTIME_ENV="$SCRIPT_DIR/.env.runtime"
if [ -f "$RUNTIME_ENV" ]; then
  set -a
  source "$RUNTIME_ENV"
  set +a
fi

NODES_DIR="${BLOCKCHAIN_DATA_ROOT:-$SCRIPT_DIR/nodes}"

BESU_IMAGE="${BESU_IMAGE:-hyperledger/besu:26.6.1}"
CHAIN_ID="${CHAIN_ID:-2025}"
BLOCKCHAIN_RUNTIME_ROLE="${BLOCKCHAIN_RUNTIME_ROLE:-all}"

VALIDATOR_NODES=(validator01 validator02 validator03 validator04)
NODES=(validator01 validator02 validator03 validator04 rpc01)
P2P_PORTS=(30303 30304 30305 30306 30307)
# Addresses on the private VPN/backbone. These are the only addresses embedded
# in static-nodes.json: host-local Docker networks are never advertised.
DEFAULT_BACKBONE_IPS=(172.31.0.11 172.31.0.12 172.31.0.21 172.31.0.22 172.31.0.31)
if [ -n "${BLOCKCHAIN_BACKBONE_IPS:-}" ]; then
  IFS=',' read -r -a BACKBONE_IPS <<< "$BLOCKCHAIN_BACKBONE_IPS"
else
  BACKBONE_IPS=("${DEFAULT_BACKBONE_IPS[@]}")
fi
if [ "${#BACKBONE_IPS[@]}" -ne "${#NODES[@]}" ]; then
  error "BLOCKCHAIN_BACKBONE_IPS must contain exactly ${#NODES[@]} comma-separated addresses"
fi

# ─── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
log()   { echo -e "${CYAN}[SETUP]${NC} $*"; }
ok()    { echo -e "${GREEN}[  OK ]${NC} $*"; }
warn()  { echo -e "${YELLOW}[ WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# =============================================================================
# 1. Check dependencies
# =============================================================================
log "Checking dependencies..."
for cmd in docker jq openssl; do
  command -v "$cmd" &>/dev/null || error "Missing dependency: $cmd"
done
docker compose version &>/dev/null || error "docker compose (v2) not found. Install the Docker Compose plugin."
ok "Dependencies OK"

# =============================================================================
# 1b. Resolve master wallet address
#     Run create-master-wallet.sh first if no master wallet exists yet.
# =============================================================================
MASTER_WALLET_FILE="$CONFIG_DIR/master-wallet"
if [ ! -f "$MASTER_WALLET_FILE" ]; then
  warn "Master wallet not found. Generating one now..."
  bash "$SCRIPT_DIR/scripts/create-master-wallet.sh"
fi
MASTER_ADDRESS=$(cat "$MASTER_WALLET_FILE" | tr -d '[:space:]')
ok "Master wallet: ${MASTER_ADDRESS}"

# =============================================================================
# 2. Detect if already initialized
# =============================================================================
if [ ! -f "$CONFIG_DIR/genesis.json" ] && [ "$BLOCKCHAIN_RUNTIME_ROLE" != "all" ]; then
  artifact_dir="${BLOCKCHAIN_RUNTIME_CHAIN_ARTIFACT_DIR:-}"
  if [ -z "$artifact_dir" ]; then
    error "Role '$BLOCKCHAIN_RUNTIME_ROLE' requires BLOCKCHAIN_RUNTIME_CHAIN_ARTIFACT_DIR containing its generated chain artifact"
  fi
  "$SCRIPT_DIR/scripts/import-role-artifact.sh" "$BLOCKCHAIN_RUNTIME_ROLE" "$artifact_dir"
fi

if [ -f "$CONFIG_DIR/genesis.json" ]; then
  for node in "${NODES[@]}"; do
    test -f "$NODES_DIR/$node/nodekey" || error "genesis.json exists but $NODES_DIR/$node/nodekey is missing; active data root is incomplete"
    test -f "$NODES_DIR/$node/static-nodes.json" || error "genesis.json exists but $NODES_DIR/$node/static-nodes.json is missing; active data root is incomplete"
  done
  warn "genesis.json already exists — setup already ran."
  warn "To start fresh: ./scripts/reset.sh && ./setup.sh"
  exit 0
fi

# =============================================================================
# 3. Create node data directories
# =============================================================================
log "Creating node data directories..."
for node in "${NODES[@]}"; do
  mkdir -p "$NODES_DIR/$node"
done
ok "Directories created"

# =============================================================================
# 4. Generate full QBFT config via operator generate-blockchain-config
#    This generates: genesis.json + 4 keypairs
# =============================================================================
log "Preparing QBFT configuration..."

QBFT_DIR="$CONFIG_DIR/qbft-gen"
mkdir -p "$QBFT_DIR"

# Configuration file for the operator (correct format for Besu 26+)
cat > "$QBFT_DIR/qbft-config.json" <<EOFJSON
{
  "genesis": {
    "config": {
      "chainId": ${CHAIN_ID},
      "berlinBlock": 0,
      "londonBlock": 0,
      "qbft": {
        "blockperiodseconds": 6,
        "epochlength": 30000,
        "requesttimeoutseconds": 10,
        "blockreward": "0"
      }
    },
    "nonce": "0x0",
    "timestamp": "0x0",
    "gasLimit": "0x1fffffffffffff",
    "difficulty": "0x1",
    "mixHash": "0x63746963616c2062797a616e74696e65206661756c7420746f6c6572616e6365",
    "coinbase": "0x0000000000000000000000000000000000000000",
    "alloc": {
      "${MASTER_ADDRESS}": {
        "balance": "0xd3c21bcecceda1000000"
      }
    }
  },
  "blockchain": {
    "nodes": {
      "generate": true,
      "count": 4
    }
  }
}
EOFJSON

log "Running besu operator generate-blockchain-config..."
QBFT_OUTPUT="$QBFT_DIR/output"
mkdir -p "$QBFT_OUTPUT"

docker run --rm \
  -v "$QBFT_DIR:/qbft" \
  --user "$(id -u):$(id -g)" \
  "$BESU_IMAGE" \
  operator generate-blockchain-config \
    --config-file=/qbft/qbft-config.json \
    --to=/qbft/output \
    --private-key-file-name=nodekey

ok "Blockchain config generated!"

# =============================================================================
# 5. Verify operator output
# =============================================================================
KEY_DIRS=("$QBFT_OUTPUT"/keys/0x*)
KEY_COUNT=${#KEY_DIRS[@]}

if [ "$KEY_COUNT" -lt 4 ]; then
  error "Expected 4 validator key sets, found $KEY_COUNT. Check $QBFT_OUTPUT"
fi

# =============================================================================
# 6. Copy genesis.json to config/
# =============================================================================
log "Copying genesis.json..."
cp "$QBFT_OUTPUT/genesis.json" "$CONFIG_DIR/genesis.json"
ok "  genesis.json → config/"

# =============================================================================
# 7. Distribute keys to each node directory
# =============================================================================
log "Distributing keys to node directories..."

ADDRESSES=()
PUBKEYS=()

for i in "${!VALIDATOR_NODES[@]}"; do
  node="${VALIDATOR_NODES[$i]}"
  key_dir="${KEY_DIRS[$i]}"
  address=$(basename "$key_dir")

  DATA_DIR="$NODES_DIR/$node"
  cp "$key_dir/nodekey"  "$DATA_DIR/nodekey"
  cp "$key_dir/key.pub"  "$DATA_DIR/nodekey.pub"

  PUBKEY=$(cat "$key_dir/key.pub" | tr -d '[:space:]' | sed 's/^0x//')
  ADDRESSES+=("$address")
  PUBKEYS+=("$PUBKEY")

  ok "  $node → $address"
done

# RPC is deliberately not a validator. Generate only its libp2p node key and
# derive the public key used in static-nodes.json.
RPC_DATA_DIR="$NODES_DIR/rpc01"
mkdir -p "$RPC_DATA_DIR"
openssl rand -hex 32 > "$RPC_DATA_DIR/nodekey"
docker run --rm \
  -v "$RPC_DATA_DIR:/data" \
  "$BESU_IMAGE" \
  public-key export \
    --node-private-key-file=/data/nodekey \
    --to=/data/nodekey.pub >/dev/null
RPC_PUBKEY=$(cat "$RPC_DATA_DIR/nodekey.pub" | tr -d '[:space:]' | sed 's/^0x//')
RPC_ADDRESS="rpc01"
PUBKEYS+=("$RPC_PUBKEY")
ADDRESSES+=("$RPC_ADDRESS")
ok "  rpc01 → non-validator peer"

# =============================================================================
# 8. Generate per-node static-nodes.json (fixed IPs, excluding self)
#    Besu 26+ requires IPs — DNS hostnames are not accepted in static-nodes.json
# =============================================================================
log "Generating static-nodes.json (fixed IPs, per node)..."

for i in "${!NODES[@]}"; do
  node="${NODES[$i]}"
  DATA_DIR="$NODES_DIR/$node"

  # Build JSON array of peers (excluding self)
  PEERS="[]"
  for j in "${!NODES[@]}"; do
    [ "$i" -eq "$j" ] && continue
    ip="${BACKBONE_IPS[$j]}"
    port="${P2P_PORTS[$j]}"
    pubkey="${PUBKEYS[$j]}"
    PEERS=$(echo "$PEERS" | jq --arg e "enode://${pubkey}@${ip}:${port}" '. + [$e]')
  done

  echo "$PEERS" > "$DATA_DIR/static-nodes.json"
  ok "  static-nodes.json → $node ($(echo "$PEERS" | jq 'length') peers)"
done

# Global version (all 4 nodes) for reference
ALL="[]"
for j in "${!NODES[@]}"; do
  ip="${BACKBONE_IPS[$j]}"
  port="${P2P_PORTS[$j]}"
  pubkey="${PUBKEYS[$j]}"
  ALL=$(echo "$ALL" | jq --arg e "enode://${pubkey}@${ip}:${port}" '. + [$e]')
done
echo "$ALL" > "$CONFIG_DIR/static-nodes.json"
ok "  global static-nodes.json → config/"

# =============================================================================
# 9. Clean up temporary files
# =============================================================================
log "Cleaning up temporary files..."
rm -rf "$QBFT_DIR"
ok "  Cleanup done"

# =============================================================================
# Summary
# =============================================================================
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  dARK blockchain runtime configured successfully! 🌑${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  Chain ID   : ${CYAN}${CHAIN_ID}${NC}"
echo -e "  Consensus  : ${CYAN}QBFT${NC}"
echo -e "  Besu       : ${CYAN}${BESU_IMAGE}${NC}"
echo ""
echo -e "  Master Wallet : ${CYAN}${MASTER_ADDRESS}${NC}"
echo -e "                  (see master-wallet.txt for credentials)"
echo ""
echo -e "  Validators:"
for i in "${!VALIDATOR_NODES[@]}"; do
  echo -e "    ${NODES[$i]} (${BACKBONE_IPS[$i]}) → ${CYAN}${ADDRESSES[$i]}${NC}"
done
echo -e "    rpc01 (${BACKBONE_IPS[4]}) → non-validator RPC peer"
echo ""
echo -e "  Start the network:"
echo -e "    ${YELLOW}docker compose up -d${NC}"
echo ""
echo -e "  Block explorer:"
echo -e "    ${YELLOW}http://localhost:25000${NC} (may take ~2 min on first boot)"
echo ""
