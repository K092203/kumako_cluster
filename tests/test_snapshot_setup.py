"""Tests for the snapshot setup hook and command environment injection."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import worker


COUNT_SETUP = (
    "import os, pathlib; "
    "p = pathlib.Path(os.environ['SUPERCON_ROOT']) / 'setup_count.txt'; "
    "p.write_text(str(int(p.read_text()) + 1 if p.exists() else 1))"
)


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    worker.ensure_layout(tmp_path)
    return tmp_path


def write_manifest(root: Path, command, timeout_sec: float = 60) -> None:
    (root / "repo_snapshot" / "cluster_setup.json").write_text(
        json.dumps({"setup_command": command, "timeout_sec": timeout_sec}),
        encoding="utf-8",
    )


def write_job(root: Path, job_id: str, **extra) -> Path:
    path = root / "jobs" / "pending" / f"{job_id}.json"
    path.write_text(json.dumps({"job_id": job_id, **extra}), encoding="utf-8")
    return path


def run_one(root: Path, worker_id: str = "worker01") -> None:
    claimed = worker.claim_job(root, worker_id)
    assert claimed is not None
    worker.process_job(root, worker_id, root / "worker_local" / worker_id, claimed)


def setup_count(root: Path) -> int:
    path = root / "setup_count.txt"
    return int(path.read_text()) if path.exists() else 0


def test_setup_runs_once_per_snapshot(root: Path) -> None:
    write_manifest(root, [sys.executable, "-c", COUNT_SETUP])
    write_job(root, "job001", command="dummy", elapsed=0.0)
    write_job(root, "job002", command="dummy", elapsed=0.0)
    run_one(root)
    run_one(root)
    assert setup_count(root) == 1
    assert (root / "jobs" / "done" / "job001.json").exists()
    assert (root / "jobs" / "done" / "job002.json").exists()


def test_setup_reruns_when_snapshot_changes(root: Path) -> None:
    write_manifest(root, [sys.executable, "-c", COUNT_SETUP])
    write_job(root, "job001", command="dummy", elapsed=0.0)
    run_one(root)
    (root / "repo_snapshot" / "solver.txt").write_text("v2", encoding="utf-8")
    write_job(root, "job002", command="dummy", elapsed=0.0)
    run_one(root)
    assert setup_count(root) == 2


def test_setup_failure_fails_job_and_retries(root: Path) -> None:
    write_manifest(root, [sys.executable, "-c", "import sys; sys.exit(1)"])
    write_job(root, "job001", command="dummy", elapsed=0.0)
    run_one(root)
    assert (root / "jobs" / "failed" / "job001.json").exists()
    result = json.loads(
        (root / "results" / "worker01" / "job001" / "result.json").read_text()
    )
    assert "setup failed" in result["error"]
    marker = root / "worker_local" / "worker01" / "repo.sha256"
    assert not marker.exists()

    write_manifest(root, [sys.executable, "-c", COUNT_SETUP])
    write_job(root, "job002", command="dummy", elapsed=0.0)
    run_one(root)
    assert (root / "jobs" / "done" / "job002.json").exists()
    assert setup_count(root) == 1
    assert marker.exists()


def test_job_env_injection_and_thread_defaults(root: Path) -> None:
    cmd = [
        sys.executable,
        "-c",
        "import os; print(os.environ['OMP_NUM_THREADS'], os.environ['SUPERCON_JOB_ID'], os.environ['SUPERCON_WORKER_ID'])",
    ]
    write_job(root, "job001", command=cmd, timeout_sec=30)
    run_one(root)
    stdout = (root / "results" / "worker01" / "job001" / "stdout.txt").read_text()
    assert stdout.split() == ["1", "job001", "worker01"]


def test_job_env_overrides_thread_defaults(root: Path) -> None:
    cmd = [sys.executable, "-c", "import os; print(os.environ['OMP_NUM_THREADS'])"]
    write_job(root, "job001", command=cmd, timeout_sec=30, env={"OMP_NUM_THREADS": "4"})
    run_one(root)
    stdout = (root / "results" / "worker01" / "job001" / "stdout.txt").read_text()
    assert stdout.strip() == "4"
