"""Generate and validate reproducible blockchain artifacts for v3 deployments."""

from __future__ import annotations

import json
import os
import re
import subprocess
import hashlib
from pathlib import Path

from .model import DeploymentPlan
from .network import chain_node_addresses, chain_node_ports, docker_lab_address


class ArtifactError(ValueError):
    """A chain artifact is missing, inconsistent or unsafe to overwrite."""


_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}$")
_PUBKEY = re.compile(r"[0-9a-fA-F]{128}$")
def _node_roles(plan: DeploymentPlan) -> dict[str, str]:
    return {
        node: definition["role"]
        for node, definition in plan.raw["blockchain"]["nodes"].items()
    }


def _node_groups(plan: DeploymentPlan) -> dict[str, str]:
    return {
        service.configuration["node_id"]: group.id
        for group in plan.groups for service_id in group.service_ids
        for service in (plan.service(service_id),)
        if service.type in {"besu-validator", "besu-rpc", "besu-observer"}
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_artifact_manifest(artifact_root: Path) -> Path:
    """Write public integrity evidence for every file in a chain artifact."""
    manifest = artifact_root / "artifact-manifest.json"
    files = {
        str(path.relative_to(artifact_root)): _sha256(path)
        for path in sorted(artifact_root.rglob("*"))
        if path.is_file() and path != manifest
    }
    _write_json(manifest, {"version": 1, "files": files})
    manifest.chmod(0o644)
    return manifest


def verify_artifact_manifest(artifact_root: Path) -> dict[str, str]:
    """Reject missing, altered or untracked files in a chain artifact."""
    manifest = artifact_root / "artifact-manifest.json"
    if not manifest.exists():
        raise ArtifactError(f"artifact manifest missing: {manifest}")
    try:
        document = json.loads(manifest.read_text())
        expected = document["files"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ArtifactError(f"invalid artifact manifest: {manifest}") from exc
    if not isinstance(expected, dict) or not all(isinstance(path, str) and isinstance(digest, str) for path, digest in expected.items()):
        raise ArtifactError(f"invalid artifact manifest: {manifest}")
    actual = {
        str(path.relative_to(artifact_root)): _sha256(path)
        for path in sorted(artifact_root.rglob("*"))
        if path.is_file() and path != manifest
    }
    if actual != expected:
        raise ArtifactError("artifact manifest does not match artifact files")
    return actual


def verify_artifact_compatibility(plan: DeploymentPlan, artifact_root: Path, master_wallet_address: str | None = None) -> None:
    """Ensure an artifact was generated for the current inventory topology."""
    context_path = artifact_root / "chain-context.json"
    if not context_path.exists():
        raise ArtifactError(f"chain context missing: {context_path}; regenerate the artifact for this inventory")
    try:
        actual = json.loads(context_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"invalid chain context: {context_path}") from exc
    expected = chain_context(plan, master_wallet_address or actual.get("master_wallet_address", ""))
    # Peer routing is mutable operational state in context v3.  Keep chain
    # identity, genesis inputs and node keys stable when only LAN/VPN paths
    # change; static nodes are regenerated from the current peer map.
    comparable_actual = dict(actual)
    comparable_expected = dict(expected)
    if actual.get("version") == expected.get("version") == 3:
        comparable_actual.pop("peerings", None)
        comparable_expected.pop("peerings", None)
    if comparable_actual != comparable_expected:
        differences = sorted({*actual.keys(), *expected.keys()})
        changed = [key for key in differences if actual.get(key) != expected.get(key)]
        detail = ", ".join(changed) or "unknown context difference"
        raise ArtifactError(
            f"chain artifact is incompatible with the current inventory ({detail}); "
            "generate a new artifact or use the original inventory"
        )


def chain_context(plan: DeploymentPlan, master_wallet_address: str) -> dict:
    if not _ADDRESS.fullmatch(master_wallet_address):
        raise ArtifactError("master wallet address must be a 20-byte hexadecimal address")
    roles = _node_roles(plan); groups = _node_groups(plan)
    peerings = plan.raw.get("peerings", {}).get("blockchain", [])
    context = {
        "version": 2,
        "deployment_id": plan.deployment_id,
        "chain_id": plan.raw["blockchain"]["chain_id"],
        "besu_image": plan.raw["images"]["besu"],
        "qbft": plan.raw["blockchain"]["qbft"],
        "master_wallet_address": master_wallet_address.lower(),
        "nodes": {
            node: {
                "role": roles[node],
                "group": groups[node],
                **({} if peerings else {"private_address": chain_node_addresses(plan)[node], "p2p_port": chain_node_ports(plan)[node]}),
            }
            for node in roles
        },
    }
    if peerings:
        context["version"] = 3
        # The same resolved edge is an IP endpoint on real hosts and a
        # network-qualified Docker DNS endpoint in a local multi-site lab.
        # Persist the rendered identity in the artifact because Besu reads
        # static-nodes before it can consult the inventory again.
        context["peerings"] = {"blockchain": [
            {
                **edge,
                "address": docker_lab_address(plan, plan.service(edge["to"]), edge["network"]) if plan.machine(plan.service(edge["to"]).machine_id).execution == "docker-lab" else edge["address"],
                "port": 30303 if plan.machine(plan.service(edge["to"]).machine_id).execution == "docker-lab" else edge["port"],
            }
            for edge in peerings
        ]}
    return context


def write_chain_bootstrap(plan: DeploymentPlan, output: Path, master_wallet_address: str) -> Path:
    """Write public QBFT generator input. It deliberately does not create keys."""
    if output.exists() and any(output.iterdir()):
        raise ArtifactError(f"chain artifact output must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    context = chain_context(plan, master_wallet_address)
    qbft = {
        "genesis": {
            "config": {
                "chainId": context["chain_id"], "berlinBlock": 0, "londonBlock": 0,
                "qbft": {
                    "blockperiodseconds": context["qbft"]["block_period_seconds"],
                    "epochlength": context["qbft"]["epoch_length"],
                    "requesttimeoutseconds": context["qbft"]["request_timeout_seconds"],
                    "blockreward": "0",
                },
            },
            "nonce": "0x0", "timestamp": "0x0", "gasLimit": "0x1fffffffffffff",
            "difficulty": "0x1", "mixHash": "0x63746963616c2062797a616e74696e65206661756c7420746f6c6572616e6365",
            "coinbase": "0x0000000000000000000000000000000000000000",
            "alloc": {context["master_wallet_address"]: {"balance": "0xd3c21bcecceda1000000"}},
        },
        "blockchain": {"nodes": {"generate": True, "count": sum(node["role"] == "validator" for node in context["nodes"].values())}},
    }
    _write_json(output / "chain-context.json", context)
    _write_json(output / "qbft-config.json", qbft)
    return output


def static_nodes(context: dict, public_keys: dict[str, str]) -> dict[str, list[str]]:
    """Build each node's peer list after keys have been generated externally."""
    expected_nodes = set(context.get("nodes", {}))
    if set(public_keys) != expected_nodes:
        raise ArtifactError("public keys must exactly match chain context nodes")
    cleaned = {node: key.lower().removeprefix("0x") for node, key in public_keys.items()}
    if not all(_PUBKEY.fullmatch(key) for key in cleaned.values()):
        raise ArtifactError("each node public key must be 128 hexadecimal characters")
    nodes = context.get("nodes")
    if not isinstance(nodes, dict) or set(nodes) != expected_nodes:
        raise ArtifactError("chain context does not contain the expected nodes")
    peerings = context.get("peerings", {}).get("blockchain")
    if peerings is not None:
        return {
            node: [
                f"enode://{cleaned[edge['to']]}@{edge['address']}:{edge['port']}"
                for edge in peerings if edge["from"] == node
            ]
            for node in nodes
        }
    return {node: [f"enode://{cleaned[peer]}@{nodes[peer]['private_address']}:{nodes[peer]['p2p_port']}" for peer in nodes if peer != node] for node in nodes}


def write_static_nodes(artifact_root: Path, public_keys: dict[str, str]) -> Path:
    context_path = artifact_root / "chain-context.json"
    if not context_path.exists():
        raise ArtifactError(f"chain context missing: {context_path}")
    context = json.loads(context_path.read_text())
    destination = artifact_root / "static-nodes"
    destination.mkdir(exist_ok=True)
    for node, peers in static_nodes(context, public_keys).items():
        _write_json(destination / f"{node}.json", peers)
        node_static = artifact_root / "nodes" / node / "static-nodes.json"
        if node_static.parent.exists():
            _write_json(node_static, peers)
            node_static.chmod(0o644)
    if (artifact_root / "nodes").exists():
        write_artifact_manifest(artifact_root)
    return destination


def initialize_chain(plan: DeploymentPlan, output: Path, master_wallet_address: str) -> Path:
    """Generate a private greenfield QBFT artifact through the pinned Besu image.

    The caller supplies the public wallet address only. Node private keys are
    created directly below the explicit output directory and must be delivered
    to their assigned host out of band.
    """
    write_chain_bootstrap(plan, output, master_wallet_address)
    config = output / "qbft-config.json"
    generated = output / "generated"
    command = (
        "docker", "run", "--rm", "-v", f"{output}:/work", "--user", f"{os.getuid()}:{os.getgid()}",
        plan.raw["images"]["besu"], "operator", "generate-blockchain-config",
        "--config-file=/work/qbft-config.json", "--to=/work/generated", "--private-key-file-name=nodekey",
    )
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode:
        raise ArtifactError(completed.stderr.strip() or "Besu failed to generate QBFT artifacts")
    key_dirs = sorted((generated / "keys").glob("0x*"))
    context = json.loads((output / "chain-context.json").read_text())
    validators = tuple(node for node, definition in context["nodes"].items() if definition["role"] == "validator")
    non_validators = tuple(node for node, definition in context["nodes"].items() if definition["role"] != "validator")
    if len(key_dirs) != len(validators):
        raise ArtifactError(f"Besu generated {len(key_dirs)} validator keys; expected {len(validators)}")
    genesis = generated / "genesis.json"
    if not genesis.exists():
        raise ArtifactError("Besu did not produce genesis.json")
    public_keys: dict[str, str] = {}
    for node, key_dir in zip(validators, key_dirs, strict=True):
        destination = output / "nodes" / node
        destination.mkdir(parents=True, exist_ok=True)
        for name, mode in (("nodekey", 0o600), ("key.pub", 0o644)):
            value = (key_dir / name).read_text().strip() + "\n"
            target = destination / name
            target.write_text(value)
            target.chmod(mode)
        public_keys[node] = (destination / "key.pub").read_text().strip().removeprefix("0x")
    for node in non_validators:
        destination = output / "nodes" / node; destination.mkdir(parents=True, exist_ok=True)
        key = destination / "nodekey"; key.write_text(__import__("secrets").token_hex(32) + "\n"); key.chmod(0o600)
        exported = subprocess.run(("docker", "run", "--rm", "-v", f"{destination}:/data", plan.raw["images"]["besu"], "public-key", "export", "--node-private-key-file=/data/nodekey", "--to=/data/key.pub"), capture_output=True, text=True)
        if exported.returncode: raise ArtifactError(exported.stderr.strip() or f"Besu failed to export the {node} public key")
        public_keys[node] = (destination / "key.pub").read_text().strip().removeprefix("0x")
    (output / "genesis.json").write_bytes(genesis.read_bytes())
    static_root = write_static_nodes(output, public_keys)
    for node in context["nodes"]:
        destination = output / "nodes" / node / "static-nodes.json"
        destination.write_bytes((static_root / f"{node}.json").read_bytes())
        destination.chmod(0o644)
    write_artifact_manifest(output)
    return output


def export_chain_group(artifact_root: Path, group: str, destination: Path) -> Path:
    """Export only the private node material assigned to one logical group."""
    verify_artifact_manifest(artifact_root)
    context = json.loads((artifact_root / "chain-context.json").read_text())
    assignments = tuple(node for node, definition in context.get("nodes", {}).items() if definition.get("group") == group)
    if not assignments:
        raise ArtifactError(f"chain artifact contains no nodes for group {group}")
    if destination.exists() and any(destination.iterdir()):
        raise ArtifactError(f"role artifact output must be empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    genesis = artifact_root / "genesis.json"
    if not genesis.exists():
        raise ArtifactError("chain artifact genesis.json is missing")
    (destination / "genesis.json").write_bytes(genesis.read_bytes())
    for node in assignments:
        source = artifact_root / "nodes" / node
        static = artifact_root / "static-nodes" / f"{node}.json"
        if not source.exists() or not static.exists():
            raise ArtifactError(f"chain artifact missing material for {node}")
        target = destination / "nodes" / node
        target.mkdir(parents=True, exist_ok=True)
        for name, mode in (("nodekey", 0o600), ("key.pub", 0o644)):
            (target / name).write_bytes((source / name).read_bytes())
            (target / name).chmod(mode)
        (target / "static-nodes.json").write_bytes(static.read_bytes())
    write_artifact_manifest(destination)
    return destination
