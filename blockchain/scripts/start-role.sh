#!/usr/bin/env bash
# Start one dARK blockchain runtime role. In developer HA each role is a separate Compose
# project on the same simulated backbone; production starts only the local role.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROLE="${1:-${BLOCKCHAIN_RUNTIME_ROLE:-all}}"

case "$ROLE" in
  rpc)
    project="dark-apps"
    profile="rpc"
    services=(rpc01)
    ;;
  validators-a)
    project="dark-bc-a"
    profile="validators-a"
    services=(validator01 validator02)
    ;;
  validators-b)
    project="dark-bc-b"
    profile="validators-b"
    services=(validator03 validator04)
    ;;
  all)
    "$0" validators-a
    "$0" validators-b
    "$0" rpc
    exit 0
    ;;
  *)
    echo "[ERROR] BLOCKCHAIN_RUNTIME_ROLE must be rpc, validators-a, validators-b or all" >&2
    exit 2
    ;;
esac

exec docker compose --project-directory "$ROOT_DIR" --project-name "$project" \
  --profile "$profile" up -d "${services[@]}"
