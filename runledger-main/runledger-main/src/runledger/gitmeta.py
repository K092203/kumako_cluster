"""Best-effort git metadata: commit, dirty, branch."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def collect(cwd: Path) -> dict[str, Any]:
    """Return {commit, dirty, branch} or {} when not a git repo."""
    inside = _git(["rev-parse", "--is-inside-work-tree"], cwd)
    if inside != "true":
        return {}
    info: dict[str, Any] = {}
    commit = _git(["rev-parse", "--short", "HEAD"], cwd)
    if commit is not None:
        info["commit"] = commit
    status = _git(["status", "--porcelain"], cwd)
    if status is not None:
        info["dirty"] = bool(status.strip())
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    if branch is not None:
        info["branch"] = branch
    return info
