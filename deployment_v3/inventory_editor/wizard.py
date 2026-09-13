"""Side-effect-free adaptation session for compact operator inventories."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
from typing import Any

from ..inventory_resolver import ResolutionResult, resolve_inventory
from ..planner import build_plan
from .document import InventoryDocument, InventoryDocumentError


def resolved_inventory_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Return a small operator-readable diff between two resolved documents."""
    result: dict[str, Any] = {}
    for key in ("machines", "services", "groups"):
        old, new = set(before.get(key, {})), set(after.get(key, {}))
        result[key] = {
            "added": sorted(new - old), "removed": sorted(old - new),
            "changed": sorted(item for item in old & new if before[key][item] != after[key][item]),
        }
    result["changed_sections"] = [key for key in ("deployment", "blockchain", "storage", "infrastructure", "settings", "components", "secrets") if before.get(key) != after.get(key)]
    return result


@dataclass(frozen=True)
class WizardReview:
    changed_sections: tuple[str, ...]
    resolved_diff: dict[str, Any]
    public_urls: tuple[dict[str, str], ...]
    private_connections: tuple[dict[str, str], ...]
    firewall: dict[str, tuple[dict[str, Any], ...]]
    groups: tuple[dict[str, Any], ...]
    phases: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class WizardQuestion:
    """One operator decision, independent from the terminal toolkit."""

    id: str
    topic: str
    title: str
    explanation: str
    consequences: str
    value: str
    kind: str = "text"


class WizardSession:
    """Transactional, toolkit-neutral editing of one operator inventory."""

    def __init__(self, document: InventoryDocument, *, force_save: bool = False):
        if document.raw.get("format") != "dark-operator-inventory":
            raise InventoryDocumentError("inventory-wizard supports only dark-operator-inventory; use inventory-edit for a complete v3 inventory")
        self.document = document
        self.force_save = force_save
        self.original_resolution = self._resolve(document.original)
        self.resolution = self._resolve(document.raw)
        self.stage = "summary"
        self.last_error: str | None = None

    def _resolve(self, raw: dict[str, Any]) -> ResolutionResult:
        return resolve_inventory(raw, source_path=self.document.path)

    def apply(self, change, *, stage: str) -> bool:
        """Apply a mutation only if the full compact contract remains valid."""
        candidate = deepcopy(self.document.raw)
        try:
            change(candidate)
            resolution = self._resolve(candidate)
        except Exception as exc:
            self.last_error = str(exc)
            return False
        self.document.raw = candidate
        self.resolution = resolution
        self.stage = stage
        self.last_error = None
        return True

    def replace_section(self, section: str, value: Any, *, stage: str) -> bool:
        return self.apply(lambda raw: raw.__setitem__(section, value), stage=stage)

    def questions(self, topic: str | None = None) -> tuple[WizardQuestion, ...]:
        """Return the guided decisions currently relevant to this inventory.

        Values are deliberately operator concepts, not serialized JSON.  The
        UI can render each question with an Input, Select or richer control.
        """
        raw = self.document.raw
        blockchain = raw["blockchain"]
        validators = blockchain["validator_groups"]
        observers = blockchain.get("observer_groups", {})
        rpc = blockchain["rpc"]
        items = [
            WizardQuestion("deployment.id", "identity", "Deployment identifier", "A stable lowercase identifier used in generated paths, Docker projects and reports.", "Changing it creates a distinct deployment state; it does not rename an installed deployment.", str(raw["deployment"]["id"])),
            WizardQuestion("deployment.label", "identity", "Human label", "A descriptive name shown in plans and reports.", "It does not affect endpoints, keys or placement.", str(raw["deployment"].get("label", ""))),
            WizardQuestion("profile", "identity", "Operating profile", "Choose local, lab or production. Production requires explicit availability objectives.", "The profile changes validation policy and warnings, never the architecture silently.", str(raw.get("profile", "local")), "choice"),
            WizardQuestion("rpc.primary", "blockchain", "Primary application RPC", "The RPC node used by applications unless an explicit service binding selects an observer or another RPC.", "Losing this node affects applications; it does not necessarily stop consensus.", str(rpc["primary"]), "choice"),
            WizardQuestion("storage.peers", "storage", "Storage peers", "Each peer creates one Kubo and one IPFS Cluster service.", "Replication limits must fit the number of peers and their machine failure domains.", str(len(raw["storage"]["peers"])), "integer"),
            WizardQuestion("storage.target_replicas", "storage", "Target storage replicas", "Desired durable copies for each published payload.", "A higher value requires enough peers and may expose machine-sharing risks.", str(raw["storage"]["replication"]["target_replicas"]), "integer"),
        ]
        for group_id, definition in validators.items():
            items.insert(3, WizardQuestion(f"validators.group.{group_id}", "blockchain", f"Validators in group {group_id}", f"Number of QBFT validators generated in the {group_id} failure domain.", "Quorum and machine-loss analysis are recalculated after this answer.", str(definition["validator_count"]), "integer"))
        for group_id, definition in observers.items():
            items.insert(3 + len(validators), WizardQuestion(f"observers.group.{group_id}", "blockchain", f"Observers in group {group_id}", f"Number of full Besu observer copies in the {group_id} group.", "Observers synchronize and may serve explicitly bound private RPC; they never count toward QBFT quorum.", str(definition["observer_count"]), "integer"))
        return tuple(item for item in items if topic is None or item.topic == topic)

    def answer(self, question_id: str, value: str) -> bool:
        """Apply one guided answer transactionally, retaining the last valid draft."""
        value = value.strip()
        if question_id == "deployment.id":
            return self.apply(lambda raw: raw["deployment"].__setitem__("id", value), stage="identity")
        if question_id == "deployment.label":
            return self.apply(lambda raw: raw["deployment"].__setitem__("label", value), stage="identity")
        if question_id == "profile":
            return self.apply(lambda raw: raw.__setitem__("profile", value), stage="identity")
        if question_id == "rpc.primary":
            return self.apply(lambda raw: raw["blockchain"]["rpc"].__setitem__("primary", value), stage="blockchain")
        try:
            number = int(value)
        except ValueError:
            self.last_error = "Enter a whole number."
            return False
        if question_id.startswith("validators.group."):
            group_id = question_id.removeprefix("validators.group.")
            return self.apply(lambda raw: raw["blockchain"]["validator_groups"][group_id].__setitem__("validator_count", number), stage="blockchain")
        if question_id.startswith("observers.group."):
            group_id = question_id.removeprefix("observers.group.")
            return self.apply(lambda raw: raw["blockchain"]["observer_groups"][group_id].__setitem__("observer_count", number), stage="blockchain")
        if question_id == "validators.total":
            self.last_error = "The wizard now asks each validator group separately."
            return False
        if question_id == "observers.total":
            groups = self.document.raw["blockchain"].get("observer_groups", {})
            if not groups:
                self.last_error = "Add an observer group from the advanced blockchain topic first."
                return False
            if len(groups) != 1:
                self.last_error = "Change individual groups from the advanced blockchain topic."
                return False
            group_id = next(iter(groups))
            return self.apply(lambda raw: raw["blockchain"]["observer_groups"][group_id].__setitem__("observer_count", number), stage="blockchain")
        if question_id == "storage.target_replicas":
            return self.apply(lambda raw: raw["storage"]["replication"].__setitem__("target_replicas", number), stage="storage")
        if question_id == "storage.peers":
            if number < 1:
                self.last_error = "At least one storage peer is required."
                return False
            return self.apply(lambda raw: self._set_storage_peer_count(raw, number), stage="storage")
        self.last_error = f"Unsupported guided question: {question_id}"
        return False

    @staticmethod
    def _set_storage_peer_count(raw: dict[str, Any], count: int) -> None:
        """Resize the simple storage topology while preserving valid placement."""
        peers = raw["storage"]["peers"]
        ordered = list(peers)
        if not ordered:
            raise ValueError("storage requires an initial peer")
        base_machine = raw["placement"][peers[ordered[0]]["group"]]
        for index in range(len(ordered) + 1, count + 1):
            peer_id = f"storage-{chr(ord('a') + index - 1)}"
            group_id = peer_id
            peers[peer_id] = {"group": group_id}
            raw["placement"][group_id] = base_machine
            ordered.append(peer_id)
        for peer_id in ordered[count:]:
            group_id = peers[peer_id]["group"]
            del peers[peer_id]
            raw["placement"].pop(group_id, None)

    def add_machine(self, machine_id: str, definition: dict[str, Any]) -> bool:
        return self.apply(lambda raw: raw.setdefault("machines", {}).__setitem__(machine_id, definition), stage="machines")

    def remove_machine(self, machine_id: str) -> bool:
        return self.apply(lambda raw: raw["machines"].pop(machine_id), stage="machines")

    def add_network(self, network_id: str, definition: dict[str, Any]) -> bool:
        return self.apply(lambda raw: raw.setdefault("networks", {}).__setitem__(network_id, definition), stage="networks")

    def remove_network(self, network_id: str) -> bool:
        return self.apply(lambda raw: raw["networks"].pop(network_id), stage="networks")

    def set_routes(self, routes: list[dict[str, Any]]) -> bool:
        return self.replace_section("routes", routes, stage="networks")

    def set_proxy(self, proxy_id: str, definition: dict[str, Any]) -> bool:
        return self.apply(lambda raw: raw.setdefault("proxies", {}).__setitem__(proxy_id, definition), stage="proxies")

    def remove_proxy(self, proxy_id: str) -> bool:
        return self.apply(lambda raw: raw["proxies"].pop(proxy_id), stage="proxies")

    def reorder_routes(self, proxy_id: str, site_index: int, order: list[int]) -> bool:
        def change(raw):
            routes = raw["proxies"][proxy_id]["sites"][site_index]["routes"]
            if sorted(order) != list(range(len(routes))):
                raise ValueError("route order must contain every route index exactly once")
            raw["proxies"][proxy_id]["sites"][site_index]["routes"] = [routes[index] for index in order]
        return self.apply(change, stage="proxies")

    def review(self) -> WizardReview:
        # build_plan intentionally reads a file; this private temporary input is
        # only a pure preview and never reaches a deployment action.
        with tempfile.TemporaryDirectory(prefix="dark-wizard-preview-") as directory:
            path = Path(directory) / "inventory.json"
            path.write_text(json.dumps(self.document.raw), encoding="utf-8")
            plan = build_plan(path)
        urls = []
        for proxy in self.document.raw.get("proxies", {}).values():
            for site in proxy.get("sites", []):
                for route in site.get("routes", []):
                    urls.append({"url": str(site.get("public_origin", "")).rstrip("/") + route.get("path", "/"), "service": route.get("service", ""), "upstream_path": route.get("upstream_path", "")})
        private = []
        firewall: dict[str, list[dict[str, Any]]] = {machine.id: [] for machine in plan.machines}
        for service in plan.services:
            if service.exposure:
                firewall[service.machine_id].append({"service": service.id, **service.exposure})
            for connection in service.connections.values():
                if connection.get("network"):
                    private.append({"consumer": service.id, "provider": connection["service"], "network": connection["network"], "protocol": connection.get("protocol", "tcp")})
        phases = tuple(dict.fromkeys(step.id.split(":", 1)[1] for step in plan.steps if step.id.startswith("readiness:")))
        return WizardReview(tuple(self.document.diff_sections()), resolved_inventory_diff(self.original_resolution.document, self.resolution.document), tuple(urls), tuple(private), {key: tuple(value) for key, value in firewall.items()}, tuple({"id": group.id, "machine": group.machine_id, "services": list(group.service_ids)} for group in plan.groups), phases, self.resolution.warnings)

    def save(self) -> Path | None:
        self._resolve(self.document.raw)
        if self.force_save and not self.document.changed:
            # A template has no destination file yet. Mark it dirty only at the
            # final save boundary so cancelling remains entirely in-memory.
            self.document.original = {}
        return self.document.save()
