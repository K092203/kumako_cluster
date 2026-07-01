"""Run-directory layout and writers (§7, §8).

A snapshot is a self-contained directory holding everything needed to do a
post-mortem of one command execution.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from runledger import config as C
from runledger.util import write_json


def make_run_id(name: str | None, when: datetime | None = None) -> str:
    ts = (when or datetime.now().astimezone()).strftime("%Y%m%d-%H%M%S")
    if name:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
        return f"{ts}-{safe}"
    return ts


def capture_env(mode: str | bool) -> dict[str, str]:
    """Capture environment variables.

    mode: False/"allow" -> allowlist only (§15); "all" -> everything.
    """
    if mode == "all":
        return dict(os.environ)
    captured: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in C.ENV_ALLOWLIST_EXACT or key.startswith(C.ENV_ALLOWLIST_PREFIXES):
            captured[key] = value
    return captured


def _is_sensitive(path: Path) -> bool:
    name = path.name.lower()
    return any(fnmatch.fnmatch(name, pat.lower()) for pat in C.SENSITIVE_PATTERNS)


def hash_input(path: Path) -> dict[str, Any] | None:
    """Return {path, sha256, bytes} for an input file, or None."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    digest = hashlib.sha256(raw).hexdigest()
    return {"path": str(path), "sha256": digest, "bytes": len(raw)}


def write_snapshot(
    run_dir: Path,
    *,
    meta: dict[str, Any],
    command: list[str],
    stdout: str,
    stderr: str,
    env: dict[str, str],
    resource: dict[str, Any],
    input_info: dict[str, Any] | None,
    input_path: Path | None = None,
    copy_input: bool = False,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)

    (run_dir / "argv.txt").write_text("\n".join(command) + "\n", encoding="utf-8")
    (run_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
    (run_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
    (run_dir / "env.txt").write_text(
        "".join(f"{k}={v}\n" for k, v in sorted(env.items())), encoding="utf-8"
    )
    (run_dir / "status.txt").write_text(
        f"{meta['outcome']} exit={meta['exit_code']} wall_sec={meta['wall_sec']}\n",
        encoding="utf-8",
    )
    write_json(run_dir / "resource.json", resource)

    if input_info is not None:
        (run_dir / "input.sha256").write_text(
            f"{input_info['sha256']}  {input_info['path']}\n", encoding="utf-8"
        )
        if copy_input and input_path is not None and not _is_sensitive(input_path):
            try:
                shutil.copy2(input_path, run_dir / f"input{input_path.suffix}")
            except OSError:
                pass

    write_json(run_dir / "meta.json", meta)


def update_latest(parent: Path, target_name: str) -> None:
    """Point parent/latest at target_name; fall back to latest.txt (§7)."""
    link = parent / "latest"
    try:
        if link.is_symlink() or link.is_file():
            link.unlink()
        elif link.exists():
            return  # a real directory named "latest"; do not clobber
        link.symlink_to(target_name, target_is_directory=True)
    except (OSError, NotImplementedError):
        (parent / "latest.txt").write_text(target_name + "\n", encoding="utf-8")
