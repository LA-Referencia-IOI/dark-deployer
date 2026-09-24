"""Interactive deployment operations console.

The console intentionally talks to the managed-deployment APIs rather than
shelling out to ``deploy.py``.  It therefore has the same snapshot authority,
exact selectors, labels and SSH routing as the non-interactive commands.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .console import ConsoleSnapshot, action_choices, service_detail, snapshot
from .runner import ApplyError, follow_service_logs, manage_service, managed_plan, read_service_logs, set_service_log_level

# The state and text helpers are re-exported so callers that imported them from
# this console keep working; their implementation lives in ``console``.
__all__ = ["ConsoleSnapshot", "action_choices", "run_operations_tui", "service_detail", "snapshot"]


REFRESH_SECONDS = 5
LOG_TAIL = 300
CONFIRM_ACTIONS = frozenset({"build", "stop", "restart", "recreate", "recreate-build", "remove", "log-debug", "log-restore"})
ACTION_LABELS = {
    "build": "Build",
    "stop": "Stop",
    "start": "Start",
    "restart": "Restart",
    "recreate": "Recreate",
    "recreate-build": "Build + recreate",
    "remove": "Remove",
    "log-debug": "Set runtime DEBUG",
    "log-restore": "Restore configured log level",
    "logs": "Logs",
}


def _textual():
    try:
        from textual.app import App, ComposeResult
        from textual.containers import Horizontal, Vertical
        from textual.widgets import Button, DataTable, Footer, Header, Label, RichLog, Static
    except ModuleNotFoundError as exc:
        raise ApplyError(
            "the interactive operations console requires Textual; install it with "
            "venv/bin/python -m pip install -r requirements-tui.txt"
        ) from exc
    return App, ComposeResult, Horizontal, Vertical, Button, DataTable, Footer, Header, Label, RichLog, Static


def _make_textual_app(project_root: Path):
    """Build the console so a test can drive it without a terminal."""
    App, ComposeResult, Horizontal, Vertical, Button, DataTable, Footer, Header, Label, RichLog, Static = _textual()

    class OperationsApp(App):
        CSS = """
        Screen { layout: vertical; }
        #content { height: 1fr; }
        #deployments { width: 31; border: round $primary; }
        #services { width: 1fr; border: round $primary; }
        #detail { width: 39; border: round $primary; padding: 1; }
        #actions { height: auto; margin-top: 1; }
        #actions Button {
            width: 1fr;
            margin: 0 1 1 0;
            background: $surface;
            color: $text-muted;
            border: none;
        }
        #actions Button:hover { background: $primary; color: $text; }
        #confirmation { height: auto; color: $warning; margin-top: 1; }
        #confirmation-actions { height: auto; margin-top: 1; }
        #confirmation-actions Button { width: 1fr; margin-right: 1; }
        #status { height: 3; padding: 0 1; }
        #logs { height: 16; border: round $primary; padding: 0 1; display: none; }
        DataTable { height: 1fr; }
        .pane-title { height: 1; padding: 0 1; text-style: bold; }
        """
        BINDINGS = [
            ("tab", "focus_next", "Next pane"),
            ("shift+tab", "focus_previous", "Previous pane"),
            ("r", "refresh", "Refresh"),
            ("l", "logs", "Logs"),
            ("L", "stream_logs", "Logs in terminal"),
            ("escape", "close_logs", "Close logs"),
            ("question_mark", "help", "Help"),
            ("q", "quit", "Quit"),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.deployment_id: str | None = None
            self.data = ConsoleSnapshot((), ())
            self._service_by_id: dict[str, dict[str, object]] = {}
            self._log_service: str | None = None
            self._confirm_action: str | None = None

        def compose(self) -> ComposeResult:
            yield Header(name="dARK deployment operations", show_clock=False)
            with Horizontal(id="content"):
                with Vertical(id="deployments"):
                    yield Label("DEPLOYMENTS", classes="pane-title")
                    yield DataTable(id="deployment-table", cursor_type="row")
                with Vertical(id="services"):
                    yield Label("SERVICES", classes="pane-title")
                    yield DataTable(id="service-table", cursor_type="row")
                with Vertical(id="detail"):
                    yield Label("DETAIL", classes="pane-title")
                    yield Static("Loading…", id="service-detail")
                    yield Label("ACTIONS", classes="pane-title")
                    with Vertical(id="actions"):
                        for action, label in ACTION_LABELS.items():
                            yield Button(label, id=f"action-{action}")
                    yield Static("", id="confirmation")
                    with Horizontal(id="confirmation-actions"):
                        yield Button("Confirm", id="confirm-action", variant="warning")
                        yield Button("Cancel", id="cancel-action")
            yield Static("Loading managed deployments…", id="status")
            yield RichLog(id="logs", wrap=True, highlight=False, markup=True)
            yield Footer()

        def on_mount(self) -> None:
            deployments = self.query_one("#deployment-table", DataTable)
            deployments.add_columns("Deployment", "State", "Run")
            services = self.query_one("#service-table", DataTable)
            services.add_columns("Service", "State", "Machine", "Type")
            self.action_refresh()
            self._clear_confirmation()
            self.set_interval(REFRESH_SECONDS, self._refresh_logs_if_open)

        def _status(self, value: str) -> None:
            self.query_one("#status", Static).update(value)

        def _selected_service(self) -> dict[str, object] | None:
            table = self.query_one("#service-table", DataTable)
            if table.row_count == 0 or table.cursor_row is None:
                return None
            try:
                key = table.get_row_at(table.cursor_row)[0]
            except (IndexError, TypeError):
                return None
            return self._service_by_id.get(str(key))

        def _render(self) -> None:
            deployment_table = self.query_one("#deployment-table", DataTable)
            service_table = self.query_one("#service-table", DataTable)
            deployment_table.clear(columns=False)
            service_table.clear(columns=False)
            for item in self.data.deployments:
                deployment_table.add_row(
                    str(item["deployment_id"]), str(item["state"]),
                    f"{item['running']}/{item['services']}", key=str(item["deployment_id"]),
                )
            self._service_by_id = {str(item["service"]): item for item in self.data.services}
            for item in self.data.services:
                service_table.add_row(
                    str(item["service"]), str(item["state"]), str(item["machine"]), str(item["type"]),
                    key=str(item["service"]),
                )
            self.query_one("#service-detail", Static).update(service_detail(self._selected_service()))
            self._render_action_buttons()
            if self.deployment_id:
                self._status(f"{self.deployment_id}: {len(self.data.services)} service(s).  r refresh · l logs · q quit")
            else:
                self._status("No managed deployments found.")

        def action_refresh(self) -> None:
            try:
                self.data = snapshot(project_root, self.deployment_id)
                if self.data.deployments and not self.deployment_id:
                    self.deployment_id = str(self.data.deployments[0]["deployment_id"])
                    self.data = snapshot(project_root, self.deployment_id)
                self._render()
            except ApplyError as exc:
                self._status(f"[red]Refresh failed:[/red] {exc}")

        def on_data_table_row_selected(self, event: Any) -> None:
            if event.data_table.id == "deployment-table":
                self.deployment_id = str(event.row_key.value)
                self.action_refresh()
            elif event.data_table.id == "service-table":
                self.query_one("#service-detail", Static).update(service_detail(self._selected_service()))
                self._render_action_buttons()

        def on_data_table_row_highlighted(self, event: Any) -> None:
            if event.data_table.id == "service-table":
                self.query_one("#service-detail", Static).update(service_detail(self._selected_service()))
                self._render_action_buttons()

        def _render_action_buttons(self) -> None:
            row = self._selected_service()
            choices = action_choices(row)
            available = set(choices)
            for action in ACTION_LABELS:
                button = self.query_one(f"#action-{action}", Button)
                button.display = action in available
                button.disabled = action not in available or self._confirm_action is not None

        def on_button_pressed(self, event: Any) -> None:
            button_id = str(event.button.id or "")
            if button_id.startswith("action-"):
                self._choose_action(button_id.removeprefix("action-"))
            elif button_id == "confirm-action":
                action, self._confirm_action = self._confirm_action, None
                self._clear_confirmation()
                if action:
                    self._run_action(action)
            elif button_id == "cancel-action":
                self._confirm_action = None
                self._clear_confirmation()
                self._status("Action cancelled.")

        def _choose_action(self, action: str) -> None:
            row = self._selected_service()
            if not row:
                return
            if action == "logs":
                self._open_logs(str(row["service"]))
                return
            if action in CONFIRM_ACTIONS:
                self._status(f"Confirmation required: {ACTION_LABELS[action].lower()} for {row['service']}.")
                self._confirm_action = action
                self._show_confirmation(str(row["service"]), action)
                return
            self._run_action(action)

        def _show_confirmation(self, service_id: str, action: str) -> None:
            self.query_one("#confirmation", Static).update(
                f"Confirm {ACTION_LABELS[action].lower()} for {service_id}?"
            )
            self.query_one("#confirmation-actions", Horizontal).display = True
            self._render_action_buttons()

        def _clear_confirmation(self) -> None:
            self.query_one("#confirmation", Static).update("")
            self.query_one("#confirmation-actions", Horizontal).display = False
            self._render_action_buttons()

        def _run_action(self, action: str) -> None:
            row = self._selected_service()
            if not row or not self.deployment_id:
                return
            actual = "recreate" if action == "recreate-build" else action
            build = action == "recreate-build"
            self._status(f"Running {ACTION_LABELS[action].lower()} for {row['service']}…")
            try:
                plan = managed_plan(project_root, self.deployment_id)
                if action == "log-debug":
                    set_service_log_level(plan, project_root, str(row["target"]), "DEBUG")
                elif action == "log-restore":
                    set_service_log_level(plan, project_root, str(row["target"]), restore=True)
                else:
                    manage_service(plan, project_root, actual, str(row["target"]), build=build)
            except ApplyError as exc:
                self._status(f"[red]{ACTION_LABELS[action]} failed:[/red] {exc}")
                return
            self._status(f"[green]{ACTION_LABELS[action]} completed:[/green] {row['service']}")
            self.action_refresh()

        def _open_logs(self, service_id: str) -> None:
            self._log_service = service_id
            logs = self.query_one("#logs", RichLog)
            logs.display = True
            self._refresh_logs_if_open()

        def action_logs(self) -> None:
            row = self._selected_service()
            if row:
                self._open_logs(str(row["service"]))

        def _refresh_logs_if_open(self) -> None:
            if not self._log_service or not self.deployment_id:
                return
            deployment_id, service_id = self.deployment_id, self._log_service
            self.run_worker(
                lambda: self._read_logs(deployment_id, service_id),
                name="service-logs", group="service-logs", exclusive=True, thread=True,
            )

        def _read_logs(self, deployment_id: str, service_id: str) -> None:
            """Read logs off the UI loop; SSH may take noticeable time."""
            try:
                plan = managed_plan(project_root, deployment_id)
                result = read_service_logs(plan, project_root, f"service:{service_id}", tail=LOG_TAIL)
            except ApplyError as exc:
                self.call_from_thread(self._show_logs, deployment_id, service_id, f"[red]{exc}[/red]")
                return
            output = str(result["output"]).rstrip() or "(no log output)"
            self.call_from_thread(self._show_logs, deployment_id, service_id, output)

        def _show_logs(self, deployment_id: str, service_id: str, output: str) -> None:
            if self.deployment_id != deployment_id or self._log_service != service_id:
                return
            logs = self.query_one("#logs", RichLog)
            at_end = logs.scroll_y >= max(0, logs.virtual_size.height - logs.size.height - 1)
            logs.clear()
            logs.write(f"[b]Logs: {service_id}[/b] — scroll with arrows, Page Up/Down, Home/End; Esc closes")
            logs.write(output)
            if at_end:
                logs.scroll_end(animate=False)

        def action_close_logs(self) -> None:
            self._log_service = None
            self.query_one("#logs", RichLog).display = False

        def action_stream_logs(self) -> None:
            """Hand the terminal over to a live log stream of the selected service.

            The stream is the same command the non-interactive ``logs`` command
            runs, so what arrives is the terminal's own scrollback: it can be
            selected, scrolled and copied without asking Textual's clipboard to
            work, which it does not on macOS Terminal.  It has to run here and
            synchronously, because suspending the app returns the terminal to its
            normal state, and an app cannot be resumed from a worker thread.
            """
            from textual.app import SuspendNotSupported  # reachable only inside a running app

            row = self._selected_service()
            if row is None:
                self._status("Select a service to stream its logs.")
                return
            service = str(row["service"])
            target = str(row["target"])
            try:
                plan = managed_plan(project_root, self.deployment_id)
            except ApplyError as exc:
                self._status(f"[red]Logs for {service}:[/red] {exc}")
                return
            failure: Exception | None = None
            try:
                with self.suspend():
                    # Nothing may escape this block.  Textual's suspend() has no
                    # try/finally around its yield, so an exception raised here
                    # skips resume_application_mode() and leaves the terminal as
                    # suspension left it.  The stream is therefore wrapped, and
                    # reported once the panel is back.
                    try:
                        follow_service_logs(plan, project_root, target, tail=LOG_TAIL)
                    except BaseException as exc:
                        # BaseException, not Exception: a Ctrl-C that reaches this
                        # process instead of the streamed child arrives as
                        # KeyboardInterrupt, and that must not skip the resume
                        # either.
                        failure = exc
            except SuspendNotSupported:
                self._status(
                    "[yellow]This terminal cannot hand itself over; run "
                    f"`deploy.py logs --deployment {self.deployment_id} --target {target}` instead.[/yellow]"
                )
                return
            self.action_refresh()
            if failure is None:
                self._status(f"Streaming {service} finished.  The panel is live again.")
            elif isinstance(failure, KeyboardInterrupt) or not str(failure):
                # Stopping a follow is not a failure, whichever process happened
                # to receive the signal.
                self._status(f"Streaming {service} interrupted.  The panel is live again.")
            else:
                self._status(f"[red]Logs for {service}:[/red] {failure}")

        def action_help(self) -> None:
            self._status(
                "Tab changes pane · r refreshes · buttons execute service actions · l opens refreshed logs "
                "in the panel · L streams the selected service's logs in the terminal, where they can be "
                "copied · Esc closes logs · q quits"
            )

    return OperationsApp()


def run_operations_tui(project_root: Path) -> None:
    """Run the terminal UI.  Imports Textual only for this optional command."""
    _make_textual_app(project_root).run()
