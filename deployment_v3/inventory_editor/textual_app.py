"""Textual implementation of the inventory editor.

Only this module imports Textual.  The document model can therefore be reused by
other frontends without making the normal deployment CLI depend on Textual.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .document import InventoryDocument, InventoryDocumentError


SECTIONS = (
    ("deployment", "Deployment"),
    ("defaults", "Defaults"),
    ("networks", "Networks"),
    ("machines", "Machines"),
    ("groups", "Server groups"),
    ("services", "Services and placement"),
    ("infrastructure", "Infrastructure policy"),
    ("blockchain", "Blockchain"),
    ("storage", "Storage"),
    ("components", "Components"),
    ("settings", "Minter and Store settings"),
    ("secrets", "Secret references"),
)


SECTION_HELP = {
    "deployment": "Version 3 inventory identity. Every cross-host connection selects its own LAN or VPN route.",
    "defaults": "Default SSH and host filesystem paths. Paths are interpreted on each destination machine.",
    "networks": "Named LAN/VPN CIDRs. Machines may have an address on more than one network.",
    "machines": "One logical or physical Docker host per entry. Use local for the controller host or ssh for a remote host.",
    "groups": "Logical server units. Use apps, validators and storage groups to preserve the five-server topology on Docker or SSH; each group maps to one machine and owns explicit services or node members.",
    "services": "Explicit placement, typed dependencies, and remote connection routes. Each remote route needs a network and tcp/udp protocol. Minter API, PostgreSQL, and all three Minter workers must share one machine.",
    "infrastructure": "Central network and port policies for Besu, Kubo, Cluster and the edge HTTP proxy.",
    "blockchain": "QBFT chain identity and the path to the per-host private chain artifact.",
    "storage": "Logical Cluster peers plus first-pin and target durability policy.",
    "components": "Repository origin and branch used to acquire every build input.",
    "settings": "Minter and Store tuning rendered into service environment files.",
    "secrets": "Controller source, destination path, strict mode and consumer services. Secret values are never displayed or stored in this editor.",
}

OPERATOR_SECTIONS = (
    ("deployment", "Deployment"), ("machines", "Machines"),
    ("placement", "Placement"), ("blockchain", "Blockchain"),
    ("storage", "Storage"), ("networks", "Networks"),
    ("routing", "Remote routing"), ("access", "Access"),
    ("secrets", "Secret sources"), ("overrides", "Advanced overrides"),
)

OPERATOR_SECTION_HELP = {
    "deployment": "Deployment identity and the installed recipe catalogue.",
    "machines": "Docker hosts and their real management and service addresses.",
    "placement": "The single source of truth for which machine runs each logical group.",
    "blockchain": "Chain identity, fixed node IDs, group assignment, and optional existing artifact.",
    "storage": "Logical storage peers, group assignment, and durability policy.",
    "networks": "Named LAN/VPN CIDRs used only when traffic crosses hosts.",
    "routing": "Select the network for each class of remote traffic.",
    "access": "Operator or public access. Internal private ports are derived from dependencies.",
    "secrets": "References to controller-side secret files; values are never displayed.",
    "overrides": "Deliberate exceptions to catalogue defaults. The resolved preview shows their effect.",
}


def _textual():
    """Import Textual lazily and return the objects needed by this frontend."""
    try:
        from textual.app import App, ComposeResult
        from textual.containers import Horizontal, VerticalScroll
        from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Select, Static
    except ModuleNotFoundError as exc:
        raise InventoryDocumentError(
            "the interactive editor requires Textual; install it with "
            "venv/bin/python -m pip install -r requirements-tui.txt"
        ) from exc
    return App, ComposeResult, Horizontal, VerticalScroll, Button, Footer, Header, Input, Label, ListItem, ListView, Select, Static


@dataclass(frozen=True)
class EditResult:
    saved: bool
    backup: str | None


def run_textual_editor(document: InventoryDocument) -> EditResult:
    """Run the Textual frontend and return without performing deployment work."""
    result = _make_textual_app(document).run()
    return result if isinstance(result, EditResult) else EditResult(saved=False, backup=None)


def _scalar_paths(value: Any, prefix: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    """Return editable leaf values while keeping object/list structure intact."""
    if isinstance(value, dict):
        result: list[tuple[tuple[str, ...], Any]] = []
        for key, child in value.items():
            result.extend(_scalar_paths(child, prefix + (str(key),)))
        return result
    if isinstance(value, list):
        result = []
        for index, child in enumerate(value):
            result.extend(_scalar_paths(child, prefix + (str(index),)))
        return result
    return [(prefix, value)]


def _path_label(path: tuple[str, ...]) -> str:
    return ".".join(path)


def _set_path(value: Any, path: tuple[str, ...], new_value: Any) -> None:
    current = value
    for part in path[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    last = path[-1]
    if isinstance(current, list):
        current[int(last)] = new_value
    else:
        current[last] = new_value


def _parse_scalar(text: str, original: Any, path: str) -> Any:
    if isinstance(original, bool):
        normalized = text.strip().lower()
        if normalized not in {"true", "false"}:
            raise InventoryDocumentError(f"{path}: use true or false")
        return normalized == "true"
    if isinstance(original, int) and not isinstance(original, bool):
        try:
            return int(text.strip())
        except ValueError as exc:
            raise InventoryDocumentError(f"{path}: use an integer") from exc
    if isinstance(original, float):
        try:
            return float(text.strip())
        except ValueError as exc:
            raise InventoryDocumentError(f"{path}: use a number") from exc
    return text


def _choices(path: tuple[str, ...]) -> tuple[str, ...] | None:
    """Return closed vocabularies used by the v3 inventory contract."""
    if path[-1:] == ("execution",):
        return ("local", "ssh", "auto")
    if path[-1:] == ("kind",) and len(path) > 1 and path[0] == "networks":
        return ("lan", "vpn")
    if path[-1:] == ("mode",) and "exposure" in path:
        return ("loopback", "private", "public")
    if path[-1:] == ("protocol",) and "connections" in path:
        return ("tcp", "udp")
    if path[-1:] == ("type",) and len(path) > 1 and path[0] == "services":
        return (
            "besu-rpc", "besu-validator", "explorer", "minter-api", "minter-worker",
            "minter-postgres", "minter-migrate", "admin-api", "resolver-api", "store-api",
            "dashboard", "dashboard-mysql", "dashboard-redis", "dashboard-migrate",
            "ipfs-kubo", "ipfs-cluster", "contracts-deploy", "rpc-probe", "edge-proxy",
        )
    if path[-1:] == ("worker",):
        return ("metadata", "replication", "chain")
    if path[-1:] == ("role",):
        return ("rpc", "validator", "storage", "apps")
    return None


def _make_textual_app(document: InventoryDocument):
    """Build the app separately so automated tests can exercise the UI shell."""
    App, ComposeResult, Horizontal, VerticalScroll, Button, Footer, Header, Input, Label, ListItem, ListView, Select, Static = _textual()

    class InventoryEditorApp(App):
        CSS = """
        Screen { layout: vertical; }
        #content { height: 1fr; }
        #sections { width: 28; border: round $primary; }
        #editor { width: 1fr; border: round $primary; }
        .field { height: 3; padding: 0 1; }
        .field-label { width: 32; padding-top: 1; }
        .field-input { width: 1fr; }
        #status { height: 3; padding: 0 1; }
        #actions { height: 3; padding: 0 1; }
        Button { margin-right: 1; }
        """
        BINDINGS = [
            ("ctrl+s", "save", "Save"),
            ("ctrl+v", "validate_current", "Validate"),
            ("ctrl+q", "quit_editor", "Quit"),
        ]

        def __init__(self) -> None:
            super().__init__()
            base_sections = OPERATOR_SECTIONS if document.raw.get("format") == "dark-operator-inventory" else SECTIONS
            self.sections = tuple(item for item in base_sections if item[0] in document.raw)
            self.section_help = OPERATOR_SECTION_HELP if document.raw.get("format") == "dark-operator-inventory" else SECTION_HELP
            self.section = self.sections[0][0]
            self.saved = False
            self.backup: str | None = None
            self._field_paths: list[tuple[str, ...]] = []

        def compose(self) -> ComposeResult:
            title = "dARK operator inventory editor" if document.raw.get("format") == "dark-operator-inventory" else "dARK deployment v3 inventory editor"
            yield Header(name=title, show_clock=False)
            with Horizontal(id="content"):
                entries = [ListItem(Label(label), id=f"section-{key}") for key, label in self.sections]
                yield ListView(*entries, id="sections")
                with VerticalScroll(id="editor"):
                    yield Static("", id="title")
                    yield Static("", id="fields")
            yield Static("Select a section, edit its fields, then validate before changing section.", id="status")
            with Horizontal(id="actions"):
                yield Button("Validate section", id="validate", variant="primary")
                yield Button("Save inventory", id="save", variant="success")
                yield Button("Discard and quit", id="quit", variant="error")
            yield Footer()

        def on_mount(self) -> None:
            self.query_one("#sections", ListView).index = 0
            self._load_section()

        def _status(self, text: str) -> None:
            self.query_one("#status", Static).update(text)

        def _load_section(self) -> None:
            section_label = dict(self.sections)[self.section]
            self.query_one("#title", Static).update(
                f"[b]{section_label}[/b]\n{self.section_help[self.section]}\n"
                "Edit the fields below. References and constraints are checked against the resolved v3 deployment contract."
            )
            fields = self.query_one("#fields", Static)
            fields.remove_children()
            self._field_paths = []
            for path, value in _scalar_paths(document.raw[self.section]):
                self._field_paths.append(path)
                field_label = Label(_path_label(path), classes="field-label")
                widget_id = "field-" + "-".join(path)
                choices = _choices(path)
                if choices:
                    input_widget = Select(
                        [(choice, choice) for choice in choices],
                        value=str(value),
                        id=widget_id,
                        classes="field-input",
                    )
                else:
                    input_widget = Input(value=str(value), id=widget_id, classes="field-input")
                fields.mount(Horizontal(field_label, input_widget, classes="field"))
            self._status(f"Editing {section_label}. Ctrl+V validates; Ctrl+S saves all changed sections.")

        def _commit_current(self) -> bool:
            try:
                candidate = deepcopy(document.raw[self.section])
                for path in self._field_paths:
                    widget_id = "#field-" + "-".join(path)
                    choices = _choices(path)
                    widget = self.query_one(widget_id, Select if choices else Input)
                    original = candidate
                    for part in path:
                        original = original[int(part)] if isinstance(original, list) else original[part]
                    selected = widget.value if choices else widget.value
                    _set_path(candidate, path, _parse_scalar(str(selected), original, _path_label(path)))
                replacement = deepcopy(document.raw)
                replacement[self.section] = candidate
                document.validate(replacement)
                document.raw = replacement
            except InventoryDocumentError as exc:
                self._status(f"[red]Not applied:[/red] {exc}")
                return False
            self._status(f"[green]{self.section} is valid.[/green] Changed sections: {', '.join(document.diff_sections()) or 'none'}")
            return True

        def on_list_view_selected(self, event) -> None:
            target = event.item.id
            if not target:
                return
            new_section = target.removeprefix("section-")
            if new_section == self.section:
                return
            if self._commit_current():
                self.section = new_section
                self._load_section()

        def on_button_pressed(self, event) -> None:
            if event.button.id == "validate":
                self._commit_current()
            elif event.button.id == "save":
                self.action_save()
            elif event.button.id == "quit":
                self.action_quit_editor()

        def action_validate_current(self) -> None:
            self._commit_current()

        def action_save(self) -> None:
            if not self._commit_current():
                return
            try:
                backup = document.save()
            except InventoryDocumentError as exc:
                self._status(f"[red]Cannot save:[/red] {exc}")
                return
            self.saved = True
            self.backup = str(backup) if backup else None
            self._status("[green]Inventory saved safely.[/green] Press Ctrl+Q to return to the terminal.")

        def action_quit_editor(self) -> None:
            self.exit(EditResult(saved=self.saved, backup=self.backup))

    return InventoryEditorApp()
