"""Toolkit-free state and text shared by the interactive consoles.

Both the operations console and the metrics panel read the same managed
deployment state and describe it with the same words.  Keeping that layer here,
without importing a terminal UI toolkit, is what stops the two frontends from
drifting into two different answers to the same question.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .runner import list_managed_deployments, list_managed_services, managed_plan
from .service_logging import runtime_log_level_capability


@dataclass(frozen=True)
class ConsoleSnapshot:
    deployments: tuple[dict[str, object], ...]
    services: tuple[dict[str, object], ...]


def snapshot(project_root: Path, deployment_id: str | None = None) -> ConsoleSnapshot:
    """Read the current managed deployment and service state for a console."""
    deployments = tuple(list_managed_deployments(project_root))
    if not deployments:
        return ConsoleSnapshot(deployments, ())
    selected = deployment_id or str(deployments[0]["deployment_id"])
    if not any(item["deployment_id"] == selected for item in deployments):
        return ConsoleSnapshot(deployments, ())
    plan = managed_plan(project_root, selected)
    return ConsoleSnapshot(deployments, tuple(list_managed_services(plan, project_root)))


def service_detail(row: dict[str, object] | None) -> str:
    """Render short, safe context for the selected service."""
    if not row:
        return "Select a deployment and a service to inspect it."
    return "\n".join((
        f"[b]{row['service']}[/b]  ({row['type']})",
        f"State: {row['state']}",
        f"Machine: {row['machine']}    Group: {row.get('group') or '-'}",
        f"Description: {row['description']}",
        f"Runtime: {row['detail']}",
    ))


def action_choices(row: dict[str, object] | None) -> tuple[str, ...]:
    """Map runtime-provided lifecycle actions to console actions."""
    if not row:
        return ()
    actions = [str(item) for item in row.get("actions", ())]
    if "recreate" in actions:
        actions.insert(actions.index("recreate") + 1, "recreate-build")
    capability = row.get("capabilities") or runtime_log_level_capability(str(row.get("type", "")))
    if row.get("state") == "running" and capability.get("runtime_log_level"):
        actions = ["log-debug", "log-restore", *actions]
    return tuple((*actions, "logs"))
