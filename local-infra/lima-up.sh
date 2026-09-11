#!/usr/bin/env bash
set -euo pipefail

usage() { echo "Usage: $0 --store PATH [--cpus N] [--memory GiB] [--disk GiB] [--yes]" >&2; exit 2; }
STORE=""
CPUS=2
MEMORY=4
DISK=30
YES=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --store) [[ $# -ge 2 ]] || usage; STORE="$2"; shift 2;;
    --cpus) CPUS="$2"; shift 2;;
    --memory) MEMORY="$2"; shift 2;;
    --disk) DISK="$2"; shift 2;;
    --yes) YES=true; shift;;
    -h|--help) usage;;
    *) usage;;
  esac
done
[[ -n "$STORE" ]] || usage
command -v limactl >/dev/null || { echo "limactl no está instalado (brew install lima)" >&2; exit 1; }
if [[ "$STORE" != /* ]]; then
  STORE="$PWD/$STORE"
fi
# Do not cd into the parent here: the store and one or more parent
# directories may legitimately be new.  mkdir below is the first operation
# that materializes them.
if [[ ! -e "$STORE" ]]; then
  if [[ "$YES" != true ]]; then
    read -r -p "Lima store '$STORE' does not exist. Create it? [Y/n] " answer
    case "${answer:-y}" in
      y|Y|yes|YES) ;;
      *) echo "[cancelled] Lima store was not created"; exit 0;;
    esac
  fi
  mkdir -p "$STORE"
  echo "[create] Lima store directory $STORE"
elif [[ ! -d "$STORE" ]]; then
  echo "[error] Lima store path exists but is not a directory: $STORE" >&2
  exit 1
fi
export LIMA_HOME="$STORE/.lima"
BASE_INSTANCE="dark-lab-base"
VMNET_SOCKET="/opt/homebrew/var/run/socket_vmnet"
if [[ ! -S "$VMNET_SOCKET" ]]; then
  echo "[error] socket_vmnet service socket not found: $VMNET_SOCKET" >&2
  echo "Start it with: sudo brew services start socket_vmnet" >&2
  exit 1
fi

# Remove only the old managed-network configuration. The unmanaged socket
# network below is provided by the already-running launchd service.
if [[ -f "$LIMA_HOME/_config/networks.yaml" ]] && grep -q 'socketVMNet:' "$LIMA_HOME/_config/networks.yaml"; then
  mv "$LIMA_HOME/_config/networks.yaml" "$LIMA_HOME/_config/networks.yaml.legacy"
  echo "[migrate] removed legacy socket_vmnet network configuration"
fi

ensure_base() {
  if [[ -d "$LIMA_HOME/$BASE_INSTANCE" ]]; then
    limactl stop "$BASE_INSTANCE" >/dev/null 2>&1 || true
    return
  fi
  echo "[base] Creating $BASE_INSTANCE once; Docker rootless provisioning can take several minutes."
  limactl start --name="$BASE_INSTANCE" --cpus="$CPUS" --memory="$MEMORY" --disk="$DISK" --set ".networks=[{\"socket\":\"$VMNET_SOCKET\"}]" template:docker
  echo "[base] Provisioned. Stopping it before cloning."
  limactl stop "$BASE_INSTANCE" >/dev/null
}

for name in dark-apps dark-blockchain-a dark-blockchain-b dark-storage-1 dark-storage-2; do
  if [[ -d "$LIMA_HOME/$name" ]] || limactl list --format '{{.Name}}' | grep -Fxq "$name"; then
    echo "[start] $name ya existe en $LIMA_HOME"
    # Migrate instances created by the former lima:shared-based script to
    # Lima's built-in network before starting them.
    config="$LIMA_HOME/$name/lima.yaml"
    if [[ -f "$config" ]] && grep -q 'lima: shared' "$config"; then
      if [[ "$(uname -s)" == "Darwin" ]]; then
        sed -i '' '/^networks:/,/^- lima: shared$/d' "$config"
      else
        sed -i '/^networks:/,/^- lima: shared$/d' "$config"
      fi
      echo "[migrate] $name: removed legacy lima:shared network"
    fi
    limactl start "$name" >/dev/null
  else
    ensure_base
    echo "[clone] $BASE_INSTANCE -> $name"
    # Cloning reuses the fully provisioned operating system and rootless
    # Docker installation. No dARK runtime data exists in the base image.
    limactl clone "$BASE_INSTANCE" "$name" --cpus="$CPUS" --memory="$MEMORY" --disk="$DISK" --set ".networks=[{\"socket\":\"$VMNET_SOCKET\"}]" --start
  fi
done

echo "[ok] Lima store: $LIMA_HOME"
limactl list
