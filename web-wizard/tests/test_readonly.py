"""The workbench must never reach the operational layer of the deployer."""

import sys

from webwizard.loader import example_directory, load_topology


def test_loading_never_imports_runner():
    # Resolving and planning is the most the workbench ever does; the module
    # that actually applies, pushes or recreates a deployment must stay unloaded.
    load_topology(example_directory() / "local-simple.json")
    assert "deployment_v3.runner" not in sys.modules


def test_loader_is_the_only_module_importing_deployment_v3():
    import pathlib
    import re

    pattern = re.compile(r"^\s*(?:from|import)\s+deployment_v3\b", re.MULTILINE)
    package_root = pathlib.Path(__file__).resolve().parents[1] / "webwizard"
    offenders = [
        source.name
        for source in package_root.rglob("*.py")
        if source.name != "loader.py" and pattern.search(source.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"deployment_v3 imported outside loader.py: {offenders}"
