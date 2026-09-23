#!/usr/bin/env bash
# =============================================================================
#  dARK blockchain runtime — scripts/create-master-wallet.sh
#  Generates a master wallet keypair and saves credentials to a txt file.
#  The master wallet address receives the full genesis balance.
#
#  Output files:
#    master-wallet.txt       — plain-text credentials (KEEP SECRET)
#    config/master-wallet    — address-only companion for the generated wallet
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.."
CONFIG_DIR="$SCRIPT_DIR/config"
OUTPUT_FILE="$SCRIPT_DIR/master-wallet.txt"
ADDRESS_FILE="$CONFIG_DIR/master-wallet"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Load the generated runtime environment.
if [ -f "$SCRIPT_DIR/.env.runtime" ]; then
  set -a; source "$SCRIPT_DIR/.env.runtime"; set +a
fi
BESU_IMAGE="${BESU_IMAGE:-hyperledger/besu:26.6.1}"

# =============================================================================
# Guard: do not overwrite an existing master wallet
# =============================================================================
if [ -f "$OUTPUT_FILE" ]; then
  echo -e "${YELLOW}[WARN] master-wallet.txt already exists.${NC}"
  echo -e "${YELLOW}       Delete it manually and re-run to generate a new wallet.${NC}"
  echo ""
  echo -e "  Address: ${CYAN}$(cat "$ADDRESS_FILE" 2>/dev/null || echo 'unknown')${NC}"
  exit 0
fi

echo ""
echo -e "${CYAN}Generating master wallet...${NC}"

# =============================================================================
# Step 1: Use besu operator to generate a single keypair
# =============================================================================
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

cat > "$TMPDIR/cfg.json" <<'EOF'
{
  "genesis": {
    "config": { "chainId": 1337, "berlinBlock": 0, "qbft": {
        "blockperiodseconds": 6, "epochlength": 30000, "requesttimeoutseconds": 10
    }},
    "nonce": "0x0", "timestamp": "0x0", "gasLimit": "0x1fffffffffffff",
    "difficulty": "0x1",
    "mixHash": "0x63746963616c2062797a616e74696e65206661756c7420746f6c6572616e6365",
    "coinbase": "0x0000000000000000000000000000000000000000",
    "alloc": {}
  },
  "blockchain": { "nodes": { "generate": true, "count": 1 } }
}
EOF

mkdir -p "$TMPDIR/out"

# Pull the image first so progress is visible (avoids silent hang on first run)
if ! docker image inspect "$BESU_IMAGE" &>/dev/null; then
  echo -e "${CYAN}[INFO] Pulling Docker image: ${BESU_IMAGE} (this may take a few minutes)...${NC}"
  docker pull "$BESU_IMAGE"
fi

docker run --rm \
  -v "$TMPDIR:/work" \
  --user "$(id -u):$(id -g)" \
  "$BESU_IMAGE" \
  operator generate-blockchain-config \
    --config-file=/work/cfg.json \
    --to=/work/out \
    --private-key-file-name=nodekey 2>/dev/null

# =============================================================================
# Step 2: Extract keypair data
# =============================================================================
KEY_DIR=("$TMPDIR/out/keys"/0x*)
ADDRESS=$(basename "${KEY_DIR[0]}")
PRIVATE_KEY=$(cat "${KEY_DIR[0]}/nodekey" | tr -d '[:space:]')
PUBLIC_KEY=$(cat "${KEY_DIR[0]}/key.pub" | tr -d '[:space:]' | sed 's/^0x//')

# =============================================================================
# Step 3: Save credentials to master-wallet.txt
# =============================================================================
mkdir -p "$CONFIG_DIR"

cat > "$OUTPUT_FILE" <<EOF
================================================================================
  dARK blockchain runtime — MASTER WALLET
  Generated: $(date -u '+%Y-%m-%d %H:%M:%S UTC')
================================================================================

  Address     : ${ADDRESS}
  Public Key  : 0x${PUBLIC_KEY}
  Private Key : ${PRIVATE_KEY}

================================================================================
  SECURITY WARNING
  ─────────────────────────────────────────────────────────────────────────────
  • This file contains the PRIVATE KEY for the master wallet.
  • Anyone with this key has FULL CONTROL over all funds.
  • Store this file securely and NEVER commit it to version control.
  • The .gitignore is already configured to exclude master-wallet.txt
================================================================================

  To import in MetaMask / any EVM wallet:
    Private Key: ${PRIVATE_KEY}

  RPC Endpoint: http://localhost:8545
  Chain ID    : ${CHAIN_ID:-2025}
================================================================================
EOF

# Save a public address-only companion for operator inspection.
echo "$ADDRESS" > "$ADDRESS_FILE"

# Restrict file permissions (owner read only)
chmod 600 "$OUTPUT_FILE"

# =============================================================================
# Summary
# =============================================================================
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Master wallet created successfully! 🔑${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  Address     : ${CYAN}${ADDRESS}${NC}"
echo -e "  Credentials : ${YELLOW}${OUTPUT_FILE}${NC} (permissions: 600, owner only)"
echo ""
echo -e "${RED}  ⚠  Keep master-wallet.txt SECRET — it contains the private key.${NC}"
echo ""
