#!/usr/bin/env python3
"""Generate a compact operator inventory for the Lima five-host lab."""
from __future__ import annotations

import json
import argparse
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples" / "operator-inventory" / "production-five-host.json"
TARGET = ROOT / "examples" / "operator-inventory" / "lima-five-host.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a Lima five-host compact operator inventory")
    parser.add_argument("--input", type=Path, default=SOURCE, help=f"source topology (default: {SOURCE})")
    parser.add_argument("--output", type=Path, default=TARGET, help=f"generated inventory (default: {TARGET})")
    parser.add_argument("--store", type=Path, help="Lima store directory (sets LIMA_HOME to STORE/.lima)")
    args = parser.parse_args()
    if args.store:
        os.environ["LIMA_HOME"] = str(args.store.expanduser().resolve() / ".lima")
    lima_home = Path(os.environ.get("LIMA_HOME", str(Path.home() / ".lima"))).expanduser().resolve()
    source = args.input.expanduser().resolve()
    target = args.output.expanduser().resolve()
    topology = json.loads(source.read_text())
    if topology.get("format") != "dark-operator-inventory":
        raise SystemExit(f"input must be a compact dark-operator-inventory: {source}")
    topology["deployment"] = {
        "id": "dark-lima-five-host",
        "label": "dARK Lima five-host acceptance lab",
    }
    names = ("dark-apps", "dark-blockchain-a", "dark-blockchain-b", "dark-storage-1", "dark-storage-2")
    addresses = {}
    ssh_ports = {}
    ssh_users = {}
    guest_homes = {}
    for name in names:
        output = subprocess.check_output(["limactl", "shell", name, "--", "sh", "-lc", "ip -4 -o addr show scope global | awk '{print $4}'"], text=True)
        values = [item.split("/")[0] for item in output.split() if "." in item and not item.startswith("192.168.5.")]
        if not values:
            raise SystemExit(f"cannot discover a private IPv4 address for Lima VM {name}")
        addresses[name] = values[0]
        ssh_ports[name] = int(subprocess.check_output(["limactl", "list", "--format", "{{.SSHLocalPort}}", name], text=True).strip())
        ssh_users[name] = subprocess.check_output(["limactl", "shell", name, "--", "id", "-un"], text=True).strip()
        guest_homes[name] = subprocess.check_output(["limactl", "shell", name, "--", "sh", "-lc", "printf %s \"$HOME\""], text=True).strip()
    if len(set(addresses.values())) != len(addresses):
        duplicates = sorted({address for address in addresses.values() if list(addresses.values()).count(address) > 1})
        raise SystemExit(
            "Lima returned duplicate guest addresses " + ", ".join(duplicates) + ". "
            "The built-in user-mode NAT is not a routable multi-VM network; "
            "use a routable Lima network (socket_vmnet) or configure explicit host-port forwarding."
        )
    # Keep strict host verification for the deployer, but isolate the lab's
    # TOFU record from the user's general SSH known_hosts. Regeneration is
    # intentional here because Lima guests may be recreated with new keys.
    known_hosts = lima_home / "_config" / "dark-deployer-known_hosts"
    fingerprints = []
    for name in names:
        scanned = subprocess.run(["ssh-keyscan", "-T", "5", "-p", str(ssh_ports[name]), "127.0.0.1"], capture_output=True, text=True)
        if scanned.returncode or not scanned.stdout.strip():
            raise SystemExit(f"cannot collect SSH host keys for Lima VM {name} on localhost:{ssh_ports[name]}: {scanned.stderr.strip() or 'no key returned'}")
        fingerprints.append(scanned.stdout)
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    known_hosts.write_text("".join(fingerprints))
    mapping = {"apps": "dark-apps", "blockchain-a": "dark-blockchain-a", "blockchain-b": "dark-blockchain-b", "storage-1": "dark-storage-1", "storage-2": "dark-storage-2"}
    for machine_id, machine in topology["machines"].items():
        host = mapping.get(machine_id)
        if not host:
            continue
        address = addresses[host]
        machine["execution"] = "ssh"
        # Lima exposes each guest through a distinct localhost SSH forward;
        # its guest lab address remains the address used by dARK services.
        machine["management_address"] = "127.0.0.1"
        machine["addresses"] = {"lab": address}
        machine.setdefault("ssh", {})
        machine["ssh"]["user"] = ssh_users[host]
        machine["ssh"]["port"] = ssh_ports[host]
        machine["ssh"]["private_key_file"] = str(lima_home / "_config" / "user")
        machine["ssh"]["known_hosts_file"] = str(known_hosts)
        machine["paths"] = {
            "workspace_root": f"{guest_homes[host]}/dark",
            # Keep the workspace absent until staging, while making the
            # parents required by the non-mutating preflight available in a
            # fresh Lima clone.
            "data_root": f"{guest_homes[host]}/dark-data",
            "secrets_root": f"{guest_homes[host]}/dark-secrets",
        }
    # A Lima lab deliberately has a single routable inter-VM network. It is
    # distinct from the production LAN/VPN model but uses the same explicit
    # operator routing contract.
    octets = addresses[names[0]].split(".")
    topology["networks"] = {"lab": {"kind": "lan", "cidr": ".".join(octets[:3]) + ".0/24"}}
    topology["routing"] = {
        "blockchain_p2p": "lab",
        "storage_p2p": "lab",
        "storage_api": "lab",
        "application_api": "lab",
    }
    topology["storage"]["cluster_name"] = "dark-lima"
    topology["proxies"] = {
        "gateway": {
            "group": "apps",
            "listener": {"bind": "public", "port": 80},
            "tls": {"mode": "http"},
            "sites": [{"host": topology["machines"]["apps"]["addresses"]["lab"], "public_origin": "http://" + topology["machines"]["apps"]["addresses"]["lab"], "routes": [
                {"id": "dashboard", "path": "/admin/", "service": "dashboard", "upstream_path": "/"},
                {"id": "explorer", "path": "/explorer/", "service": "explorer", "upstream_path": "/"},
                {"id": "minter-v1", "path": "/api/v1/", "service": "minter-api", "upstream_path": "/api/v1/"},
                {"id": "minter-docs", "path": "/api/docs", "service": "minter-api", "upstream_path": "/docs"},
                {"id": "minter-openapi", "path": "/api/openapi.json", "service": "minter-api", "upstream_path": "/openapi.json"},
                {"id": "resolver", "path": "/", "service": "resolver-api", "upstream_path": "/api/v1/arks/"},
            ]}],
        }
    }
    # Production examples contain intentionally unusable controller-side
    # placeholders. The Lima lab either generates these during installation
    # or receives explicit sources from the operator after review.
    topology.pop("secrets", None)
    artifact = topology.get("blockchain", {}).get("artifact")
    if isinstance(artifact, dict):
        artifact.pop("source", None)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(topology, indent=2) + "\n")
    print(f"generated {target}")


if __name__ == "__main__":
    main()
