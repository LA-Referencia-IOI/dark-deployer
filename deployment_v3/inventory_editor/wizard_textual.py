"""Textual UI for the compact inventory adaptation wizard."""

from __future__ import annotations

from .document import InventoryDocumentError
from .wizard import WizardSession

STAGES = (("summary", "Summary"), ("deployment", "Identity and defaults"), ("machines", "Machines"), ("networks", "Networks and routing"), ("placement", "Placement"), ("proxies", "Public access"), ("operations", "Operational values"), ("review", "Review and save"))
STAGE_SECTIONS = {"deployment": ("deployment", "defaults"), "machines": ("machines",), "networks": ("networks", "routing", "routes"), "placement": ("placement",), "proxies": ("proxies",), "operations": ("blockchain", "storage", "secrets", "overrides")}


def run_wizard(session: WizardSession):
    try:
        from textual.app import App, ComposeResult
        from textual.containers import Horizontal
        from textual.widgets import Button, Footer, Header, Input, Static
    except ModuleNotFoundError as exc:
        raise InventoryDocumentError("the interactive wizard requires Textual; install it with venv/bin/python -m pip install -r requirements-tui.txt") from exc

    class WizardApp(App):
        CSS = "#question { height: 1fr; padding: 1 2; } #answer { margin: 1 2; } #actions { height: 3; padding: 0 2; } #status { height: 3; padding: 0 2; }"
        BINDINGS = [("ctrl+q", "quit", "Cancel"), ("ctrl+s", "save", "Save from review"), ("left", "previous", "Back"), ("right", "next", "Continue")]
        def __init__(self): super().__init__(); self.index = 0; self.save_armed = False
        def compose(self) -> ComposeResult:
            yield Header(name="dARK inventory adaptation wizard", show_clock=False)
            yield Static("", id="question")
            yield Input(placeholder="Write your answer and press Enter", id="answer")
            with Horizontal(id="actions"):
                yield Button("Back", id="back")
                yield Button("Continue", id="next")
                yield Button("Review", id="review")
            yield Static("Review is required before saving.", id="status")
            yield Footer()
        def on_mount(self): self.render_question()
        def render_question(self):
            questions = session.questions()
            question = questions[self.index]
            self.query_one("#question", Static).update(f"[b]{self.index + 1}/{len(questions)} · {question.title}[/b]\n\n{question.explanation}\n\n[b]Consequences:[/b] {question.consequences}")
            answer = self.query_one("#answer", Input); answer.value = question.value; answer.focus()
        def commit(self):
            question = session.questions()[self.index]
            if not session.answer(question.id, self.query_one("#answer", Input).value):
                self.query_one("#status", Static).update("[red]Not applied:[/red] " + (session.last_error or "invalid answer")); return False
            self.query_one("#status", Static).update("[green]Answer accepted. The draft was revalidated.[/green]"); return True
        def action_next(self):
            if not self.commit():
                return
            if self.index < len(session.questions()) - 1:
                self.index += 1
                self.render_question()
            else:
                self.show_review()
        def action_previous(self):
            if self.index > 0: self.index -= 1; self.render_question()
        def on_input_submitted(self, event): self.action_next()
        def on_button_pressed(self, event):
            if event.button.id == "back": self.action_previous()
            elif event.button.id == "next": self.action_next()
            elif event.button.id == "review":
                self.show_review()
        def show_review(self):
            review = session.review()
            self.query_one("#question", Static).update("[b]Review[/b]\n\n" + "Warnings: " + "; ".join(review.warnings or ("none",)) + "\nGroups: " + ", ".join(group["id"] for group in review.groups) + "\nChanged sections: " + ", ".join(review.changed_sections or ("none",)) + "\n\nPress Ctrl+S twice to save.")
            self.query_one("#answer", Input).display = False
        def action_save(self):
            if self.query_one("#answer", Input).display:
                self.query_one("#status", Static).update("[yellow]Open Review before saving.[/yellow]"); return
            if not self.save_armed:
                self.save_armed = True
                self.query_one("#status", Static).update("[yellow]Review complete. Press Ctrl+S again to confirm save.[/yellow]")
                return
            backup=session.save(); self.query_one("#status", Static).update("[green]Saved safely.[/green]" + (" Backup: " + str(backup) if backup else ""))
        def action_quit(self): self.exit()
    WizardApp().run()
