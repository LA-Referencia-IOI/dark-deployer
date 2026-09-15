#!/usr/bin/env python3
"""Generate the compact operator inventory for the Lima two-site lab."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples/operator-inventory/production-five-host.json"
TARGET = ROOT / "examples/operator-inventory/lima-two-site-five-host.json"
NODES = {
    "apps": ("dark-two-site-apps", "site-a", "site-a-lan", "10.241.10.10", "10.250.30.10"),
    "blockchain-a": ("dark-two-site-blockchain-a", "site-a", "site-a-lan", "10.241.10.11", "10.250.30.11"),
    "storage-1": ("dark-two-site-storage-1", "site-a", "site-a-lan", "10.241.10.12", "10.250.30.12"),
    "blockchain-b": ("dark-two-site-blockchain-b", "site-b", "site-b-lan", "10.241.20.11", "10.250.30.21"),
    "storage-2": ("dark-two-site-storage-2", "site-b", "site-b-lan", "10.241.20.12", "10.250.30.22"),
}
def shell(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the Lima two-site compact operator inventory")
    parser.add_argument("--input", type=Path, default=SOURCE)
    parser.add_argument("--output", type=Path, default=TARGET)
    parser.add_argument("--store", type=Path, required=True, help="Lima store used by lima-two-site-up.sh")
    args = parser.parse_args()
    store = args.store.expanduser().resolve()
    os.environ["LIMA_HOME"] = str(store / ".lima")
    topology = json.loads(args.input.expanduser().resolve().read_text())
    topology.update({"format_version": 3, "profile": "lab", "deployment": {"id": "dark-lima-two-site-five-host", "label": "dARK Lima two-site five-host acceptance lab"}})
    topology["networks"] = {
        "site-a-lan": {"kind": "lan", "cidr": "10.241.10.0/24"},
        "site-b-lan": {"kind": "lan", "cidr": "10.241.20.0/24"},
        "mesh-vpn": {"kind": "vpn", "cidr": "10.250.30.0/24"},
    }
    topology["sites"] = {"site-a": {"lan": "site-a-lan"}, "site-b": {"lan": "site-b-lan"}}
    known_hosts = store / ".lima/_config/dark-deployer-two-site-known_hosts"
    records: list[str] = []
    public_hosts: dict[str, str] = {}
    for machine, (instance, site, lan, lan_address, vpn_address) in NODES.items():
        observed = shell("limactl", "shell", instance, "--", "sh", "-lc", "ip -4 addr show")
        for address in (lan_address, vpn_address):
            if f"inet {address}/" not in observed:
                raise SystemExit(f"{instance} does not have {address}; run local-infra/lima-two-site-up.sh first")
        port = int(shell("limactl", "list", "--format", "{{.SSHLocalPort}}", instance))
        user = shell("limactl", "shell", instance, "--", "id", "-un")
        home = shell("limactl", "shell", instance, "--", "sh", "-lc", "printf %s \"$HOME\"")
        scan = subprocess.run(["ssh-keyscan", "-T", "5", "-p", str(port), "127.0.0.1"], text=True, capture_output=True)
        if scan.returncode or not scan.stdout.strip():
            raise SystemExit(f"cannot collect SSH host key for {instance}: {scan.stderr.strip() or 'no key returned'}")
        records.append(scan.stdout)
        public_hosts[machine] = shell("limactl", "shell", instance, "--", "sh", "-lc", "ip route get 1.1.1.1 | awk 'NR==1 {print $7}'")
        definition = topology["machines"][machine]
        definition.update({"execution": "ssh", "site": site, "management_address": "127.0.0.1", "addresses": {lan: lan_address, "mesh-vpn": vpn_address}})
        definition["ssh"] = {"user": user, "port": port, "private_key_file": str(store / ".lima/_config/user"), "known_hosts_file": str(known_hosts)}
        definition["paths"] = {"workspace_root": f"{home}/dark", "data_root": f"{home}/dark-data", "secrets_root": f"{home}/dark-secrets"}
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    known_hosts.write_text("".join(records))
    topology["routing"] = {key: {"same_site": "site_lan", "cross_site": "mesh-vpn"} for key in ("blockchain_p2p", "storage_p2p", "storage_api", "application_api")}
    topology["storage"]["cluster_name"] = "dark-lima-two-site"
    topology["proxies"]["gateway"]["tls"] = {"mode": "http"}
    proxy_site = topology["proxies"]["gateway"]["sites"][0]
    proxy_site["host"] = public_hosts["apps"]
    proxy_site["public_origin"] = f"http://{public_hosts['apps']}"
    topology.pop("secrets", None)
    topology["blockchain"].get("artifact", {}).pop("source", None)
    output = args.output.expanduser().resolve()
    output.write_text(json.dumps(topology, indent=2) + "\n")
    print(f"generated {output}")


if __name__ == "__main__":
    main()
