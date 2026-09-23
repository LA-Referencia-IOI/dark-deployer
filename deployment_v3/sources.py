"""Evidence and validation for the public source trees deployed by v2."""

from __future__ import annotations

import subprocess
import hashlib
import os
from pathlib import Path

from .model import DeploymentPlan


class SourceError(RuntimeError):
    """A source checkout cannot be identified or differs from the inventory."""


COMPONENT_PATHS = {
    "dark-dapp": "components/dark-dapp",
    "dark-explorador": "components/dark-explorador",
    "dashboard-web": "components/dashboard-web",
    "dark-core-lib": "components/dark-core-lib",
    "dark-core-admin-api": "components/dark-core-admin-api",
    "dark-core-minter-api": "components/dark-core-minter-api",
    "dark-core-resolver-api": "components/dark-core-resolver-api",
    "dark-store-api": "components/dark-store-api",
    "dark-ipfs": "components/dark-ipfs",
    "dark-monitoring": "components/dark-monitoring",
}

_IGNORED_SOURCE_ENTRIES = frozenset({".git", ".env", ".env.integration", ".generated", ".DS_Store", "venv", ".venv", "node_modules", "__pycache__", ".pytest_cache"})


def source_tree_digest(path: Path) -> str:
    """Hash the same public tree that staging is allowed to distribute."""
    digest = hashlib.sha256()
    for directory, names, files in os.walk(path, topdown=True, followlinks=False):
        names[:] = sorted(name for name in names if name not in _IGNORED_SOURCE_ENTRIES)
        relative_directory = Path(directory).relative_to(path)
        for name in sorted(files):
            if name in _IGNORED_SOURCE_ENTRIES:
                continue
            item = Path(directory) / name
            if item.is_symlink() and not item.exists():
                continue
            relative = (relative_directory / name).as_posix().encode()
            digest.update(relative + b"\0")
            with item.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def _git(path: Path, *arguments: str) -> str:
    completed = subprocess.run(("git", "-C", str(path), *arguments), capture_output=True, text=True)
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise SourceError(f"cannot inspect Git source {path}: {detail}")
    return completed.stdout.strip()


def source_evidence(plan: DeploymentPlan, project_root: Path, component_ids: set[str] | None = None) -> dict[str, dict[str, object]]:
    """Return reproducible source evidence and enforce an explicitly chosen ref.

    The controller transfers its validated public sources to remote machines;
    it deliberately does not let remote hosts choose another branch.  A commit
    hash records the exact result while the inventory branch remains the human
    release reference.
    """
    evidence: dict[str, dict[str, object]] = {}
    selected = component_ids if component_ids is not None else set(plan.raw["components"])
    for component_id in sorted(selected):
        relative = COMPONENT_PATHS.get(component_id)
        if relative is None:
            raise SourceError(f"no source path is registered for component {component_id}")
        path = project_root / relative
        if not path.is_dir():
            raise SourceError(f"component source is missing: {component_id} at {path}")
        branch = _git(path, "branch", "--show-current")
        expected = plan.raw["components"][component_id].get("branch")
        if expected and branch != expected:
            raise SourceError(f"component {component_id} is on {branch or 'detached HEAD'}, expected {expected}")
        remote = _git(path, "remote", "get-url", "origin")
        expected_remote = plan.raw["components"][component_id]["repository_url"]
        evidence[component_id] = {
            "branch": branch or None,
            "commit": _git(path, "rev-parse", "HEAD"),
            "dirty": bool(_git(path, "status", "--porcelain")),
            "origin": remote,
            "inventory_repository": expected_remote,
            "inventory_branch": expected or None,
            "content_sha256": source_tree_digest(path),
        }
    return evidence


def require_matching_source_evidence(
    pushed: dict[str, dict[str, object]], current: dict[str, dict[str, object]]
) -> None:
    """Reject applying a delivered bundle from a different source revision.

    A branch is intentionally a moving delivery reference. Once ``push`` has
    staged a public bundle, the subsequent apply must use exactly the commits
    recorded with it. Dirty state is evidence too: changing it could make a
    local build differ from the sources delivered to a remote host.
    """
    if pushed == current:
        return
    changed = [
        component_id
        for component_id in sorted(set(pushed) | set(current))
        if pushed.get(component_id) != current.get(component_id)
    ]
    raise SourceError(
        "source evidence changed since push; create a new push before apply"
        + (f" ({', '.join(changed)})" if changed else "")
    )
