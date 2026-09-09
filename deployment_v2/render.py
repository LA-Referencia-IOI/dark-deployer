"""Render public deployment artifacts. Rendering never contacts Docker or SSH."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from .model import DeploymentPlan, Group


def _json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _group_document(plan: DeploymentPlan, group: Group) -> dict:
    machine = plan.machine(group.machine_id)
    return {
        "version": 2,
        "deployment_id": plan.deployment_id,
        "group": {"id": group.id, "kind": group.kind, "members": list(group.members), "explorer": group.explorer},
        "machine": {
            "id": machine.id, "execution": machine.execution, "management_address": machine.management_address,
            "private_address": machine.private_address, "workspace_root": machine.workspace_root,
            "data_root": machine.data_root, "secrets_root": machine.secrets_root,
            "docker_subnet": plan.docker_subnets[machine.id],
        },
        "endpoints": [endpoint.__dict__ | {"url": endpoint.url} for endpoint in plan.endpoints],
        "components": plan.raw["components"], "settings": plan.raw["settings"],
        "secrets": plan.raw["secrets"], "exposure": plan.raw["exposure"],
        "blockchain": plan.raw["blockchain"], "storage": plan.raw["storage"],
    }


def render_plan(plan: DeploymentPlan, output: Path) -> Path:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"render output must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    shared = output / "shared"
    shared.mkdir()
    shutil.copyfile(plan.inventory_path, shared / "deployment-topology.json")
    (shared / "plan.json").write_text(_json({
        "deployment_id": plan.deployment_id, "docker_subnets": plan.docker_subnets,
        "endpoints": [endpoint.__dict__ | {"url": endpoint.url} for endpoint in plan.endpoints],
        "steps": [step.__dict__ for step in plan.steps],
    }))
    groups_root = output / "groups"
    groups_root.mkdir()
    manifest: dict[str, str] = {}
    for group in plan.groups:
        target = groups_root / group.id
        target.mkdir()
        config = target / "group.json"
        config.write_text(_json(_group_document(plan, group)))
        manifest[str(config.relative_to(output))] = _sha256(config)
    (output / "manifest.json").write_text(_json({"version": 2, "files": manifest}))
    return output
