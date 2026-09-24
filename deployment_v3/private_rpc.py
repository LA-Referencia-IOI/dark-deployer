"""Private JSON-RPC transport shared by managed service operations."""

from __future__ import annotations

import json

from .network import internal_port


def private_json_rpc_command(deployment_id: str, machine_id: str, service, method: str, params: list | None = None) -> tuple[str, ...]:
    """Build a JSON-RPC request from the service's private Docker network."""
    payload = json.dumps(
        {"jsonrpc": "2.0", "method": method, "params": params or [], "id": 1},
        separators=(",", ":"),
    )
    return (
        "docker", "run", "--rm", "--network", f"{deployment_id}-{machine_id}",
        "curlimages/curl:8.12.1", "-fsS", "--max-time", "5",
        "-H", "Content-Type: application/json", "--data", payload,
        f"http://{service.id}:{internal_port(service)}",
    )
