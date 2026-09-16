"""Coverage for the operations console, driven by the Textual pilot.

Only the read-only paths are exercised here.  The lifecycle actions (build,
stop, restart, recreate, remove) are deliberately left to the manual workflow:
stubbing the runner out of the way to test them would prove nothing about the
commands that actually reach a host.  What is covered is the state the console
reads, the log pane, and the terminal handover for a live log stream, which is
the one action whose failure mode is a terminal left in a bad state.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import textual.app
from deployment_v3 import operations_tui
from deployment_v3.planner import build_plan
from deployment_v3.render import render_plan
from deployment_v3.runner import ApplyError


ROOT = Path(__file__).resolve().parents[1]

try:
    import textual  # noqa: F401

    TEXTUAL_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - depends on the optional extra
    TEXTUAL_AVAILABLE = False


class RecordingSuspend:
    """Stand-in for ``App.suspend`` that reports whether the block raised.

    A real ``suspend`` with an exception inside its block skips
    ``resume_application_mode()``, so the console must never let one escape.
    """

    def __init__(self) -> None:
        self.entered = False
        self.escaped: BaseException | None = None

    def __enter__(self):
        self.entered = True
        return None

    def __exit__(self, exc_type, exc, traceback):
        self.escaped = exc_type
        return False


@unittest.skipUnless(TEXTUAL_AVAILABLE, "Textual is optional (requirements-tui.txt)")
class OperationsConsoleTests(unittest.IsolatedAsyncioTestCase):
    def prepared_project_root(self, plan=None) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        if plan is not None:
            render_plan(plan, root / ".generated" / "deployment-v3" / plan.deployment_id / "bundle")
        return root

    def local_ha(self) -> object:
        return build_plan(ROOT / "examples" / "operator-inventory" / "local-ha.json")

    async def test_console_reads_a_deployment_and_lists_its_services(self):
        plan = self.local_ha()
        app = operations_tui._make_textual_app(self.prepared_project_root(plan))
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            deployments = app.query_one("#deployment-table")
            services = app.query_one("#service-table")
            self.assertEqual(deployments.row_count, 1)
            self.assertEqual(deployments.get_row_at(0)[0], plan.deployment_id)
            self.assertEqual(services.row_count, len(plan.services))

    async def test_the_terminal_handover_is_bound_and_labelled(self):
        """It must be discoverable: the clipboard path it replaces is not."""
        app = operations_tui._make_textual_app(self.prepared_project_root())
        self.assertIn(("L", "stream_logs", "Logs in terminal"), type(app).BINDINGS)

    async def test_stream_logs_hands_the_terminal_over_for_the_selected_service(self):
        plan = self.local_ha()
        app = operations_tui._make_textual_app(self.prepared_project_root(plan))
        calls: list[tuple[str, int]] = []
        suspend = RecordingSuspend()

        def fake_follow(plan, project_root, target, *, tail):
            calls.append((target, tail))
            return {"service": target}

        with patch.object(operations_tui, "follow_service_logs", fake_follow), \
             patch.object(textual.app.App, "suspend", lambda self: suspend):
            async with app.run_test() as pilot:
                await pilot.pause(0.2)
                service = app.query_one("#service-table").get_row_at(0)[0]
                await pilot.press("L")
                await pilot.pause(0.1)
                status = str(app.query_one("#status").content)
                self.assertIn("live again", status)
                self.assertEqual(app.screen.id, "_default")

        self.assertEqual(calls, [(f"service:{service}", operations_tui.LOG_TAIL)])
        self.assertTrue(suspend.entered)
        self.assertIsNone(suspend.escaped)

    async def test_stream_logs_without_a_selection_explains_itself(self):
        app = operations_tui._make_textual_app(self.prepared_project_root())
        with patch.object(operations_tui, "follow_service_logs") as follow:
            async with app.run_test() as pilot:
                await pilot.pause(0.1)
                await pilot.press("L")
                await pilot.pause(0.1)
                self.assertIn("Select a service", str(app.query_one("#status").content))
        follow.assert_not_called()

    async def test_a_failed_stream_is_reported_and_never_escapes_the_suspend_block(self):
        plan = self.local_ha()
        app = operations_tui._make_textual_app(self.prepared_project_root(plan))
        suspend = RecordingSuspend()

        def failing_follow(plan, project_root, target, *, tail):
            raise ApplyError("no managed container found for minter-api")

        with patch.object(operations_tui, "follow_service_logs", failing_follow), \
             patch.object(textual.app.App, "suspend", lambda self: suspend):
            async with app.run_test() as pilot:
                await pilot.pause(0.2)
                await pilot.press("L")
                await pilot.pause(0.1)
                status = str(app.query_one("#status").content)
                self.assertIn("no managed container found", status)
                # Still usable afterwards: the tree is intact and can re-render.
                app.action_refresh()
                self.assertEqual(app.query_one("#service-table").row_count, len(plan.services))

        # The terminal handover must complete normally: an exception here would
        # leave the driver without resume_application_mode().
        self.assertTrue(suspend.entered)
        self.assertIsNone(suspend.escaped)

    async def test_an_unexpected_stream_error_does_not_escape_either(self):
        plan = self.local_ha()
        app = operations_tui._make_textual_app(self.prepared_project_root(plan))
        suspend = RecordingSuspend()

        def exploding_follow(plan, project_root, target, *, tail):
            raise OSError("ssh: command not found")

        with patch.object(operations_tui, "follow_service_logs", exploding_follow), \
             patch.object(textual.app.App, "suspend", lambda self: suspend):
            async with app.run_test() as pilot:
                await pilot.pause(0.2)
                await pilot.press("L")
                await pilot.pause(0.1)
                self.assertIn("ssh: command not found", str(app.query_one("#status").content))

        self.assertIsNone(suspend.escaped)

    async def test_an_unsupported_terminal_is_told_what_to_run(self):
        plan = self.local_ha()
        app = operations_tui._make_textual_app(self.prepared_project_root(plan))

        def refuse(self):
            raise textual.app.SuspendNotSupported("not supported here")

        with patch.object(operations_tui, "follow_service_logs"), \
             patch.object(textual.app.App, "suspend", refuse):
            async with app.run_test() as pilot:
                await pilot.pause(0.2)
                await pilot.press("L")
                await pilot.pause(0.1)
                status = str(app.query_one("#status").content)
                self.assertIn("cannot hand itself over", status)
                self.assertIn("deploy.py logs", status)


if __name__ == "__main__":
    unittest.main()
