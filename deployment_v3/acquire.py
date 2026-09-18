"""Acquire component checkouts declared by a deployment topology."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .model import DeploymentPlan
from .sources import COMPONENT_PATHS


class AcquisitionError(RuntimeError):
    """A component could not be cloned or advanced to its inventory branch."""


def _repository_urls(url: str) -> tuple[str, ...]:
    """Return the declared Git URL followed by a public HTTPS equivalent."""
    candidates = [url]
    if url.startswith("git@github.com:"):
        candidates.append("https://github.com/" + url.removeprefix("git@github.com:"))
    elif url.startswith("ssh://git@github.com/"):
        candidates.append("https://github.com/" + url.removeprefix("ssh://git@github.com/"))
    return tuple(dict.fromkeys(candidate.rstrip("/") for candidate in candidates))


def _same_repository(left: str, right: str) -> bool:
    return {url.rstrip("/").removesuffix(".git") for url in _repository_urls(left)}.intersection(
        {url.rstrip("/").removesuffix(".git") for url in _repository_urls(right)}
    ) != set()


def _run(argv: tuple[str, ...], *, cwd: Path | None = None) -> str:
    result = subprocess.run(argv, cwd=cwd, text=True, capture_output=True)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "no command output"
        raise AcquisitionError(f"{' '.join(argv)}: {detail}")
    return result.stdout.strip()


def _branch_exists(url: str, branch: str) -> bool:
    result = subprocess.run(("git", "ls-remote", "--exit-code", "--heads", url, branch), capture_output=True)
    if result.returncode == 0:
        return True
    if result.returncode == 2:
        return False
    detail = result.stderr.decode(errors="replace").strip() or "remote unavailable"
    raise AcquisitionError(f"cannot inspect branch {branch} at {url}: {detail}")


def _reachable_branch_url(url: str, branch: str) -> str:
    errors: list[str] = []
    for candidate in _repository_urls(url):
        try:
            if _branch_exists(candidate, branch):
                return candidate
        except AcquisitionError as exc:
            errors.append(str(exc))
    if errors:
        raise AcquisitionError("repository is unreachable through all configured transports: " + " | ".join(errors))
    raise AcquisitionError(f"branch '{branch}' does not exist for repository {url}")


def acquire_components(plan: DeploymentPlan, project_root: Path, *, update_existing: bool = True) -> dict[str, str]:
    """Clone or fast-forward every component to the branch in the inventory.

    Existing working-tree changes are never overwritten.  A checkout is only
    advanced through a fast-forward merge, so a diverged or dirty branch fails
    with an actionable error instead of silently replacing operator work.
    """
    commits: dict[str, str] = {}
    for component_id in sorted(plan.raw["components"]):
        relative = COMPONENT_PATHS.get(component_id)
        if relative is None:
            raise AcquisitionError(f"no checkout path registered for {component_id}")
        definition = plan.raw["components"][component_id]
        url = definition["repository_url"]
        branch = definition["branch"]
        target = project_root / relative
        if not target.exists():
            clone_url = _reachable_branch_url(url, branch)
            target.parent.mkdir(parents=True, exist_ok=True)
            _run(("git", "clone", "--branch", branch, "--single-branch", "--", clone_url, str(target)))
        else:
            if not (target / ".git").is_dir():
                raise AcquisitionError(f"component path is not a Git repository: {target}")
            origin = _run(("git", "remote", "get-url", "origin"), cwd=target)
            if not _same_repository(origin, url):
                raise AcquisitionError(f"component {component_id} origin differs: {origin} != {url}")
            if not update_existing:
                local = _run(("git", "branch", "--show-current"), cwd=target)
                if local != branch:
                    raise AcquisitionError(
                        f"component {component_id} is on '{local or 'detached HEAD'}'; "
                        f"switch to '{branch}' or choose update"
                    )
                commits[component_id] = _run(("git", "rev-parse", "HEAD"), cwd=target)
                print(f"[OK] Source kept: {component_id} ({branch}) {commits[component_id][:12]}")
                continue
            dirty = subprocess.run(("git", "status", "--porcelain"), cwd=target, capture_output=True, text=True)
            if dirty.stdout.strip():
                print(f"[WARNING] {component_id} has local changes; preserving them while updating the branch")
            fetch_url = _reachable_branch_url(url, branch)
            if not _same_repository(origin, fetch_url):
                _run(("git", "remote", "set-url", "origin", fetch_url), cwd=target)
            _run(("git", "fetch", "origin", f"{branch}:refs/remotes/origin/{branch}"), cwd=target)
            local = _run(("git", "branch", "--show-current"), cwd=target)
            if local != branch:
                _run(("git", "switch", branch), cwd=target)
            _run(("git", "merge", "--ff-only", f"origin/{branch}"), cwd=target)
        commits[component_id] = _run(("git", "rev-parse", "HEAD"), cwd=target)
        print(f"[OK] Source ready: {component_id} ({branch}) {commits[component_id][:12]}")
    return commits
