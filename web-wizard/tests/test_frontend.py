"""Headless canvas checks (layouts, dragging, editing, persistence).

Optional: skips unless ``node`` can resolve ``jsdom``. Enable it with

    cd web-wizard/tests/frontend && npm install

or by pointing ``NODE_PATH`` at an existing jsdom install.

The fixtures are generated here with the real draft session, so the browser side
is exercised against graphs the deployer actually produced.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from webwizard.loader import example_directory
from webwizard.operations import perform
from webwizard.session import DraftSession

FRONTEND = Path(__file__).resolve().parent / "frontend"
STATIC = Path(__file__).resolve().parents[1] / "webwizard" / "static"


def _jsdom_available() -> bool:
    if not shutil.which("node"):
        return False
    probe = subprocess.run(
        ["node", "-e", "require.resolve('jsdom')"],
        cwd=str(FRONTEND),
        capture_output=True,
    )
    return probe.returncode == 0


@pytest.mark.skipif(
    not _jsdom_available(),
    reason="node + jsdom not available (cd tests/frontend && npm install)",
)
def test_canvas_layouts_dragging_and_editing(tmp_path: Path) -> None:
    session = DraftSession.open(example_directory() / "production-six-host.json")
    # Declared reachability, so the fabric view has a route to draw.
    perform(session, "add_route", {"from": "lan", "to": "vpn", "via": "site-vpn"})
    before = session.view()
    perform(session, "move_group", {"group": "blockchain-b", "machine": "apps"})
    after = session.view()

    fixtures = tmp_path / "fixtures.json"
    fixtures.write_text(
        json.dumps({
            "graph": before,
            "movedGraph": after,
            "moveOperation": {
                "kind": "move_group",
                "params": {"group": "blockchain-b", "machine": "apps"},
            },
        }),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["node", "canvas.test.js", str(STATIC), str(fixtures)],
        cwd=str(FRONTEND),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
