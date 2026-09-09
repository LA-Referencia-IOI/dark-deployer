"""Generate and validate reproducible blockchain artifacts for v2 deployments."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .model import DeploymentPlan


class ArtifactError(ValueError):
    """A chain artifact is missing, inconsistent or unsafe to overwrite."""


_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}$")
_PUBKEY = re.compile(r"[0-9a-fA-F]{128}$")
_VALIDATORS = ("validator01", "validator02", "validator03", "validator04")
_NODES = (*_VALIDATORS, "rpc01")
_P2P_PORTS = {node: 30303 + index for index, node in enumerate(_NODES)}


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def chain_context(plan: DeploymentPlan, master_wallet_address: str) -> dict:
    if not _ADDRESS.fullmatch(master_wallet_address):
        raise ArtifactError("master wallet address must be a 20-byte hexadecimal address")
    node_to_machine = {
        member: plan.machine(group.machine_id)
        for group in plan.groups for member in group.members
        if group.kind in {"apps", "validators"}
    }
    return {
        "version": 1,
        "deployment_id": plan.deployment_id,
        "chain_id": plan.raw["blockchain"]["chain_id"],
        "besu_image": plan.raw["blockchain"]["besu_image"],
        "master_wallet_address": master_wallet_address.lower(),
        "nodes": {
            node: {
                "validator": node in _VALIDATORS,
                "private_address": node_to_machine[node].private_address,
                "p2p_port": _P2P_PORTS[node],
            }
            for node in _NODES
        },
    }


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
                "qbft": {"blockperiodseconds": 6, "epochlength": 30000, "requesttimeoutseconds": 10, "blockreward": "0"},
            },
            "nonce": "0x0", "timestamp": "0x0", "gasLimit": "0x1fffffffffffff",
            "difficulty": "0x1", "mixHash": "0x63746963616c2062797a616e74696e65206661756c7420746f6c6572616e6365",
            "coinbase": "0x0000000000000000000000000000000000000000",
            "alloc": {context["master_wallet_address"]: {"balance": "0xd3c21bcecceda1000000"}},
        },
        "blockchain": {"nodes": {"generate": True, "count": 4}},
    }
    _write_json(output / "chain-context.json", context)
    _write_json(output / "qbft-config.json", qbft)
    return output


def static_nodes(context: dict, public_keys: dict[str, str]) -> dict[str, list[str]]:
    """Build each node's peer list after keys have been generated externally."""
    if set(public_keys) != set(_NODES):
        raise ArtifactError("public keys must contain validator01..validator04 and rpc01")
    cleaned = {node: key.lower().removeprefix("0x") for node, key in public_keys.items()}
    if not all(_PUBKEY.fullmatch(key) for key in cleaned.values()):
        raise ArtifactError("each node public key must be 128 hexadecimal characters")
    nodes = context.get("nodes")
    if not isinstance(nodes, dict) or set(nodes) != set(_NODES):
        raise ArtifactError("chain context does not contain the expected nodes")
    return {
        node: [
            f"enode://{cleaned[peer]}@{nodes[peer]['private_address']}:{nodes[peer]['p2p_port']}"
            for peer in _NODES if peer != node
        ]
        for node in _NODES
    }


def write_static_nodes(artifact_root: Path, public_keys: dict[str, str]) -> Path:
    context_path = artifact_root / "chain-context.json"
    if not context_path.exists():
        raise ArtifactError(f"chain context missing: {context_path}")
    context = json.loads(context_path.read_text())
    destination = artifact_root / "static-nodes"
    destination.mkdir(exist_ok=True)
    for node, peers in static_nodes(context, public_keys).items():
        _write_json(destination / f"{node}.json", peers)
    return destination
