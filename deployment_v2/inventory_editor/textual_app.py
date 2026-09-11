"""Textual implementation of the inventory editor.

Only this module imports Textual.  The document model can therefore be reused by
other frontends without making the normal deployment CLI depend on Textual.
"""

from __future__ import annotations

from dataclasses import dataclass

from .document import InventoryDocument, InventoryDocumentError


SECTIONS = (
    ("deployment", "Deployment"),
    ("defaults", "Defaults"),
    ("networks", "Networks"),
    ("machines", "Machines"),
    ("groups", "Groups"),
    ("blockchain", "Blockchain"),
    ("storage", "Storage"),
    ("components", "Components"),
    ("settings", "Minter and Store settings"),
    ("secrets", "Secret references"),
    ("exposure", "Exposure"),
)


def _textual():
    """Import Textual lazily and return the objects needed by this frontend."""
    try:
        from textual.app import App, ComposeResult
        from textual.containers import Horizontal, VerticalScroll
        from textual.widgets import Button, Footer, Header, Label, ListItem, ListView, Static, TextArea
    except ModuleNotFoundError as exc:
        raise InventoryDocumentError(
            "the interactive editor requires Textual; install it with "
            "venv/bin/python -m pip install -r requirements-tui.txt"
        ) from exc
    return App, ComposeResult, Horizontal, VerticalScroll, Button, Footer, Header, Label, ListItem, ListView, Static, TextArea


@dataclass(frozen=True)
class EditResult:
    saved: bool
    backup: str | None


def run_textual_editor(document: InventoryDocument) -> EditResult:
    """Run the Textual frontend and return without performing deployment work."""
    result = _make_textual_app(document).run()
    return result if isinstance(result, EditResult) else EditResult(saved=False, backup=None)


def _make_textual_app(document: InventoryDocument):
    """Build the app separately so automated tests can exercise the UI shell."""
    App, ComposeResult, Horizontal, VerticalScroll, Button, Footer, Header, Label, ListItem, ListView, Static, TextArea = _textual()

    class InventoryEditorApp(App):
        CSS = """
        Screen { layout: vertical; }
        #content { height: 1fr; }
        #sections { width: 28; border: round $primary; }
        #editor { width: 1fr; border: round $primary; }
        #status { height: 3; padding: 0 1; }
        TextArea { height: 1fr; }
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
            self.section = "deployment"
            self.saved = False
            self.backup: str | None = None

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            with Horizontal(id="content"):
                entries = [ListItem(Label(label), id=f"section-{key}") for key, label in SECTIONS]
                yield ListView(*entries, id="sections")
                with VerticalScroll(id="editor"):
                    yield Static("", id="title")
                    yield TextArea("", id="section-json")
            yield Static("Select a section, edit its JSON, then validate before changing section.", id="status")
            with Horizontal(id="actions"):
                yield Button("Validate section", id="validate", variant="primary")
                yield Button("Save inventory", id="save", variant="success")
                yield Button("Discard and quit", id="quit", variant="error")
            yield Footer()

        def on_mount(self) -> None:
            self.query_one("#sections", ListView).index = 0
            self._load_section()

        def _area(self):
            return self.query_one("#section-json", TextArea)

        def _status(self, text: str) -> None:
            self.query_one("#status", Static).update(text)

        def _load_section(self) -> None:
            label = dict(SECTIONS)[self.section]
            self.query_one("#title", Static).update(
                f"[b]{label}[/b]\nEdit this complete JSON section. References and constraints are checked by the deployment validator."
            )
            self._area().text = document.section_text(self.section)
            self._status(f"Editing {label}. Ctrl+V validates; Ctrl+S saves all changed sections.")

        def _commit_current(self) -> bool:
            try:
                document.replace_section_text(self.section, self._area().text)
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
