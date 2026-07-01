"""Execute one command and persist it as a snapshot (§5.1, §6.1)."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from runledger import config as C
from runledger import gitmeta, resource, snapshot
from runledger.measure import MeasureResult, MeasureSpec, extract_measure


@dataclass
class RunOutcome:
    exit_code: int
    outcome: str
    wall_sec: float
    stdout: str
    stderr: str
    resource: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunRecord:
    run_id: str
    run_dir: Path
    outcome: RunOutcome
    measure: MeasureResult
    meta: dict[str, Any]


def _decode(data: bytes | None) -> str:
    if not data:
        return ""
    return data.decode("utf-8", errors="replace")


def _classify(returncode: int) -> str:
    if returncode == 0:
        return C.OUTCOME_COMPLETED
    if returncode < 0:
        return C.OUTCOME_KILLED
    return C.OUTCOME_FAILED


def execute(
    command: list[str],
    *,
    cwd: Path,
    timeout: float | None = None,
    input_path: Path | None = None,
    extra_env: dict[str, str] | None = None,
    shell: bool = False,
) -> RunOutcome:
    """Run a command, capturing stdout/stderr/wall/exit/outcome/resource."""
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    stdin_data: bytes | None = None
    if input_path is not None:
        try:
            stdin_data = input_path.read_bytes()
        except OSError:
            stdin_data = None

    popen_cmd: Any = " ".join(command) if shell else command
    before = resource.snapshot()
    start = time.monotonic()
    try:
        proc = subprocess.run(
            popen_cmd,
            cwd=cwd,
            input=stdin_data,
            capture_output=True,
            timeout=timeout,
            shell=shell,
            env=env,
        )
    except FileNotFoundError:
        wall = time.monotonic() - start
        return RunOutcome(
            exit_code=C.EXIT_MISSING_BIN,
            outcome=C.OUTCOME_MISSING_BIN,
            wall_sec=round(wall, 6),
            stdout="",
            stderr=f"command not found: {command[0] if command else ''}",
            resource=resource.delta(before, resource.snapshot()),
        )
    except subprocess.TimeoutExpired as exc:
        wall = time.monotonic() - start
        return RunOutcome(
            exit_code=C.EXIT_TIMEOUT,
            outcome=C.OUTCOME_TIMEOUT,
            wall_sec=round(wall, 6),
            stdout=_decode(exc.stdout),
            stderr=_decode(exc.stderr),
            resource=resource.delta(before, resource.snapshot()),
        )

    wall = time.monotonic() - start
    return RunOutcome(
        exit_code=proc.returncode,
        outcome=_classify(proc.returncode),
        wall_sec=round(wall, 6),
        stdout=_decode(proc.stdout),
        stderr=_decode(proc.stderr),
        resource=resource.delta(before, resource.snapshot()),
    )


def build_meta(
    *,
    run_id: str,
    name: str | None,
    command: list[str],
    shell: bool,
    timeout: float | None,
    cwd: Path,
    outcome: RunOutcome,
    measure: MeasureResult,
    git: dict[str, Any],
    input_info: dict[str, Any] | None,
    tags: dict[str, str] | None,
    when: datetime | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": C.SCHEMA_VERSION,
        "run_id": run_id,
        "name": name,
        "created_at": (when or datetime.now().astimezone()).isoformat(timespec="seconds"),
        "cwd": str(cwd),
        "command": command,
        "shell": shell,
        "timeout_sec": timeout,
        "exit_code": outcome.exit_code,
        "outcome": outcome.outcome,
        "wall_sec": outcome.wall_sec,
        "git": git,
        "input": input_info,
        "measure": measure.to_dict(),
        "tags": tags or {},
    }


def run_once(
    command: list[str],
    *,
    out_root: Path,
    name: str | None = None,
    run_id: str | None = None,
    timeout: float | None = None,
    input_path: Path | None = None,
    capture_env_mode: str | bool = False,
    extra_env: dict[str, str] | None = None,
    cwd: Path | None = None,
    shell: bool = False,
    measure_spec: MeasureSpec | None = None,
    copy_input: bool = False,
    tags: dict[str, str] | None = None,
    update_latest: bool = True,
) -> RunRecord:
    """Execute a command and write a full snapshot under out_root/<run_id>/."""
    cwd = (cwd or Path.cwd()).resolve()
    spec = measure_spec or MeasureSpec()
    rid = run_id or snapshot.make_run_id(name)
    run_dir = out_root / rid

    outcome = execute(
        command,
        cwd=cwd,
        timeout=timeout,
        input_path=input_path,
        extra_env=extra_env,
        shell=shell,
    )

    measure = extract_measure(
        spec,
        stdout=outcome.stdout,
        stderr=outcome.stderr,
        exit_code=outcome.exit_code,
        base=cwd,
    )

    git = gitmeta.collect(cwd)
    input_info = hash_input(input_path) if input_path is not None else None
    env_captured = snapshot.capture_env(capture_env_mode)

    meta = build_meta(
        run_id=rid,
        name=name,
        command=command,
        shell=shell,
        timeout=timeout,
        cwd=cwd,
        outcome=outcome,
        measure=measure,
        git=git,
        input_info=input_info,
        tags=tags,
    )

    snapshot.write_snapshot(
        run_dir,
        meta=meta,
        command=command,
        stdout=outcome.stdout,
        stderr=outcome.stderr,
        env=env_captured,
        resource=outcome.resource,
        input_info=input_info,
        input_path=input_path,
        copy_input=copy_input,
    )

    if update_latest:
        snapshot.update_latest(out_root, rid)

    return RunRecord(run_id=rid, run_dir=run_dir, outcome=outcome, measure=measure, meta=meta)


def hash_input(path: Path) -> dict[str, Any] | None:
    return snapshot.hash_input(path)
