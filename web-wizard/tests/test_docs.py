"""The documentation must not drift away from the code.

Counts that change with every unrelated edit — test totals, file sizes — are
deliberately *absent* from the docs, so nothing here checks them. What is
checked is the structural claims, which have to stay true: every operation
documented and no others, every endpoint documented, and the invariants that
were expensive to discover still written down where a newcomer will find them.
"""

from __future__ import annotations

import re
from pathlib import Path

from webwizard.operations import operations

PACKAGE = Path(__file__).resolve().parents[1]
DEVELOPMENT = PACKAGE / "DEVELOPMENT.md"
README = PACKAGE / "README.md"
SERVER = PACKAGE / "webwizard" / "server.py"


def _table_names(text: str, header: str) -> set[str]:
    """The backticked names in the first column of a markdown table."""
    lines = text.splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith(header))
    names: set[str] = set()
    for line in lines[start + 2:]:
        if not line.startswith("|"):
            break
        names |= set(re.findall(r"`([a-z_]+)`", line.split("|")[1]))
    return names


def test_every_operation_is_documented_and_no_others() -> None:
    documented = _table_names(DEVELOPMENT.read_text(encoding="utf-8"), "| Operation | Parameters |")
    assert documented == set(operations()), (
        "the operations table in DEVELOPMENT.md is out of date: "
        f"undocumented={sorted(set(operations()) - documented)} "
        f"stale={sorted(documented - set(operations()))}"
    )


def _normalise(path: str) -> str:
    path = path.split("?")[0].rstrip("/") or "/"
    path = re.sub(r"\{[^}]*\}", ":id", path)
    return re.sub(r"<[^>]*>", ":id", path)


def test_every_endpoint_is_documented() -> None:
    text = DEVELOPMENT.read_text(encoding="utf-8")
    documented = {
        (method, _normalise(path))
        for method, path in re.findall(r"^\| `(GET|POST)` \| `([^`]+)`", text, re.MULTILINE)
    }
    source = SERVER.read_text(encoding="utf-8")
    real = {
        (method.upper(), _normalise(path))
        for method, path in re.findall(r'@app\.(get|post)\(\s*"([^"]+)"', source)
    }
    real.add(("GET", "/static/:id"))  # the mounted directory, documented as /static/<name>
    assert documented == real, (
        "the API table in DEVELOPMENT.md is out of date: "
        f"undocumented={sorted(real - documented)} stale={sorted(documented - real)}"
    )


def test_the_hard_won_invariants_are_still_written_down() -> None:
    development = DEVELOPMENT.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")

    # Key order is meaning: sorting the document on save is a correctness bug.
    assert "sort_keys" in development
    assert "positional" in development
    # Never overwrite, and never touch the inventory being edited.
    assert "O_CREAT" in readme or "never overwrite" in readme.lower()
    # The read-only boundary and its enforcement.
    assert "deployment_v3.runner" in readme and "deployment_v3.runner" in development
    # The two documents, which the whole design rests on.
    assert "compact operator inventory" in development
    assert "resolved v3 document" in development
