#!/usr/bin/env bash
# Create a five-VM Lima laboratory with two site LANs and a WireGuard mesh.
set -euo pipefail

usage() { echo "Usage: $0 --store PATH [--cpus N] [--memory GiB] [--disk GiB] [--yes]" >&2; exit 2; }
STORE=""; CPUS=2; MEMORY=4; DISK=30; YES=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --store) [[ $# -ge 2 ]] || usage; STORE="$2"; shift 2 ;;
    --cpus) CPUS="$2"; shift 2 ;;
    --memory) MEMORY="$2"; shift 2 ;;
    --disk) DISK="$2"; shift 2 ;;
    --yes) YES=true; shift ;;
    -h|--help) usage ;;
    *) usage ;;
  esac
done
[[ -n "$STORE" ]] || usage
command -v limactl >/dev/null || { echo "limactl no está instalado (brew install lima)" >&2; exit 1; }
command -v openssl >/dev/null || { echo "openssl es necesario para crear las claves WireGuard" >&2; exit 1; }
[[ "$STORE" = /* ]] || STORE="$PWD/$STORE"
if [[ ! -e "$STORE" ]]; then
  if [[ "$YES" != true ]]; then
    read -r -p "Lima store '$STORE' does not exist. Create it? [Y/n] " answer
    [[ "${answer:-y}" =~ ^[Yy]([Ee][Ss])?$ ]] || { echo "[cancelled]"; exit 0; }
  fi
  mkdir -p "$STORE"
fi
[[ -d "$STORE" ]] || { echo "[error] store is not a directory: $STORE" >&2; exit 1; }
export LIMA_HOME="$STORE/.lima"
BASE_INSTANCE="dark-two-site-base"
VMNET_SOCKET="/opt/homebrew/var/run/socket_vmnet"
[[ -S "$VMNET_SOCKET" ]] || { echo "[error] socket_vmnet service socket not found: $VMNET_SOCKET" >&2; echo "Start it with: sudo brew services start socket_vmnet" >&2; exit 1; }

# name | site | site-LAN address | WireGuard address | UDP port
NODES=(
  'dark-two-site-apps|site-a|10.241.10.10|10.250.30.10|51820'
  'dark-two-site-blockchain-a|site-a|10.241.10.11|10.250.30.11|51821'
  'dark-two-site-storage-1|site-a|10.241.10.12|10.250.30.12|51822'
  'dark-two-site-blockchain-b|site-b|10.241.20.11|10.250.30.21|51823'
  'dark-two-site-storage-2|site-b|10.241.20.12|10.250.30.22|51824'
)
STATE="$LIMA_HOME/_config/dark-two-site"
mkdir -p "$STATE"; chmod 700 "$STATE"

ensure_base() {
  if [[ -d "$LIMA_HOME/$BASE_INSTANCE" ]]; then limactl stop "$BASE_INSTANCE" >/dev/null 2>&1 || true; return; fi
  echo "[base] Creating $BASE_INSTANCE once; Docker provisioning can take several minutes."
  limactl start --name="$BASE_INSTANCE" --cpus="$CPUS" --memory="$MEMORY" --disk="$DISK" --set ".networks=[{\"socket\":\"$VMNET_SOCKET\"}]" template:docker
  limactl stop "$BASE_INSTANCE" >/dev/null
}

for entry in "${NODES[@]}"; do
  IFS='|' read -r name _site _lan _vpn _port <<< "$entry"
  if [[ -d "$LIMA_HOME/$name" ]] || limactl list --format '{{.Name}}' | grep -Fxq "$name"; then
    echo "[start] $name already exists"; limactl start "$name" >/dev/null
  else
    ensure_base; echo "[clone] $BASE_INSTANCE -> $name"
    limactl clone "$BASE_INSTANCE" "$name" --cpus="$CPUS" --memory="$MEMORY" --disk="$DISK" --set ".networks=[{\"socket\":\"$VMNET_SOCKET\"}]" --start
  fi
done

# socket_vmnet is only the transport underlay. dARK selects site LAN or wg0.
for entry in "${NODES[@]}"; do
  IFS='|' read -r name _site lan vpn port <<< "$entry"
  echo "[network] preparing $name"
  # Lima's docker template defaults to rootless Docker. RootlessKit collapses
  # distinct host-IP publishes onto loopback, so it cannot expose one Besu P2P
  # port simultaneously on the site LAN and wg0. This lab alone uses Docker
  # Engine's rootful daemon, which preserves the requested bind addresses.
  guest_user="$(limactl shell "$name" -- id -un)"
  # The template starts rootlesskit outside the user systemd session. Stopping
  # only docker.service therefore does not reliably remove its dockerd child.
  # Stop that user-owned daemon explicitly before reusing containerd rootfully.
  limactl shell "$name" -- sudo sh -lc "pkill -u '$guest_user' -x dockerd 2>/dev/null || true; pkill -u '$guest_user' -f 'rootlesskit.*dockerd' 2>/dev/null || true"
  limactl shell "$name" -- sudo systemctl unmask containerd.service
  limactl shell "$name" -- sudo systemctl enable --now containerd.service
  limactl shell "$name" -- sudo systemctl unmask docker.service docker.socket
  limactl shell "$name" -- sudo systemctl enable --now docker.service
  limactl shell "$name" -- sudo usermod -aG docker "$guest_user"
  limactl shell "$name" -- sh -lc 'systemctl --user disable --now docker.service docker.socket 2>/dev/null || true; docker context use default >/dev/null'
  limactl shell "$name" -- sudo sh -lc 'command -v wg >/dev/null || (apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y wireguard-tools)'
  iface="$(limactl shell "$name" -- sh -lc "ip route show default | awk 'NR==1 {print \$5}'")"
  [[ -n "$iface" ]] || { echo "[error] cannot find default interface in $name" >&2; exit 1; }
  underlay="$(limactl shell "$name" -- sh -lc "ip -4 -o addr show dev '$iface' scope global | awk 'NR==1 {split(\$4, a, \"/\"); print a[1]}'")"
  [[ -n "$underlay" ]] || { echo "[error] cannot find socket_vmnet address in $name" >&2; exit 1; }
  printf '%s\n' "$underlay" > "$STATE/$name.underlay"
  unit="[Unit]\nDescription=dARK Lima two-site LAN alias\nAfter=network-online.target\n\n[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/usr/sbin/ip address replace $lan/24 dev $iface\nExecStop=/usr/sbin/ip address del $lan/24 dev $iface\n\n[Install]\nWantedBy=multi-user.target\n"
  printf '%b' "$unit" | limactl shell "$name" -- sudo tee /etc/systemd/system/dark-lima-site-lan.service >/dev/null
  limactl shell "$name" -- sudo systemctl daemon-reload
  limactl shell "$name" -- sudo systemctl enable --now dark-lima-site-lan.service
  limactl shell "$name" -- sudo systemctl restart dark-lima-site-lan.service
  if [[ ! -f "$STATE/$name.private" ]]; then umask 077; openssl rand -base64 32 > "$STATE/$name.private"; fi
  private_key="$(<"$STATE/$name.private")"
  printf '%s\n' "$private_key" | limactl shell "$name" -- wg pubkey > "$STATE/$name.public"
done

for entry in "${NODES[@]}"; do
  IFS='|' read -r name _site _lan vpn port <<< "$entry"
  config="[Interface]\nAddress = $vpn/24\nListenPort = $port\nPrivateKey = $(<"$STATE/$name.private")\n"
  for peer in "${NODES[@]}"; do
    IFS='|' read -r peer_name _peer_site _peer_lan peer_vpn peer_port <<< "$peer"
    [[ "$peer_name" = "$name" ]] && continue
    config+="\n[Peer]\nPublicKey = $(<"$STATE/$peer_name.public")\nAllowedIPs = $peer_vpn/32\nEndpoint = $(<"$STATE/$peer_name.underlay"):$peer_port\nPersistentKeepalive = 25\n"
  done
  printf '%b' "$config" | limactl shell "$name" -- sudo tee /etc/wireguard/wg0.conf >/dev/null
  limactl shell "$name" -- sudo chmod 600 /etc/wireguard/wg0.conf
  limactl shell "$name" -- sudo systemctl enable --now wg-quick@wg0
  limactl shell "$name" -- sudo systemctl restart wg-quick@wg0
done

echo "[ok] Two-site lab ready in $LIMA_HOME"
echo "     site-a: apps, blockchain-a (validator01/02), storage-1"
echo "     site-b: blockchain-b (validator03/04), storage-2"
echo "     Next: venv/bin/python local-infra/generate-lima-two-site-inventory.py --store $STORE"
