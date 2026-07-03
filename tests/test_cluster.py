"""Tests for claim exclusivity, job outcome classification, and requeue."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import requeue_failed
import worker


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    worker.ensure_layout(tmp_path)
    return tmp_path


def write_job(root: Path, job_id: str, bucket: str = "pending", **extra) -> Path:
    path = root / "jobs" / bucket / f"{job_id}.json"
    path.write_text(json.dumps({"job_id": job_id, **extra}), encoding="utf-8")
    return path


def test_claim_is_exclusive(root: Path) -> None:
    for i in range(20):
        write_job(root, f"job{i:03d}")
    claimed = []
    for w in [f"worker{n:02d}" for n in range(1, 6)]:
        while True:
            got = worker.claim_job(root, w)
            if got is None:
                break
            claimed.append(got.stem.split("--", 1)[0])
    assert len(claimed) == 20
    assert len(set(claimed)) == 20
    assert not list((root / "jobs" / "pending").glob("*.json"))


def test_process_job_success_goes_to_done(root: Path) -> None:
    write_job(root, "job001", command="dummy", seed=1, elapsed=0.0)
    claimed = worker.claim_job(root, "worker01")
    worker.process_job(root, "worker01", root / "worker_local" / "worker01", claimed)
    assert (root / "jobs" / "done" / "job001.json").exists()
    result = json.loads((root / "results" / "worker01" / "job001" / "result.json").read_text())
    assert result["outcome"] == "completed"
    assert result["measure"]["score"] is not None


def test_process_job_failure_goes_to_failed(root: Path) -> None:
    cmd = [sys.executable, "-c", "import sys; sys.exit(3)"]
    write_job(root, "job002", command=cmd, timeout_sec=30)
    claimed = worker.claim_job(root, "worker01")
    worker.process_job(root, "worker01", root / "worker_local" / "worker01", claimed)
    assert (root / "jobs" / "failed" / "job002.json").exists()
    result = json.loads((root / "results" / "worker01" / "job002" / "result.json").read_text())
    assert result["outcome"] == "failed"
    assert result["measure"] == {"elapsed": None, "score": None, "correct": None}


def test_process_job_timeout_classified(root: Path) -> None:
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
    write_job(root, "job003", command=cmd, timeout_sec=1)
    claimed = worker.claim_job(root, "worker01")
    worker.process_job(root, "worker01", root / "worker_local" / "worker01", claimed)
    assert (root / "jobs" / "failed" / "job003.json").exists()
    result = json.loads((root / "results" / "worker01" / "job003" / "result.json").read_text())
    assert result["outcome"] == "timeout"


def test_requeue_failed_moves_back_to_pending(root: Path) -> None:
    write_job(root, "job004", bucket="failed")
    sys_argv = sys.argv
    sys.argv = ["requeue_failed.py", "--root", str(root)]
    try:
        assert requeue_failed.main() == 0
    finally:
        sys.argv = sys_argv
    assert (root / "jobs" / "pending" / "job004.json").exists()
    assert not (root / "jobs" / "failed" / "job004.json").exists()


def test_requeue_stale_running_recovers_orphans(root: Path) -> None:
    path = write_job(root, "job005", bucket="running")
    orphan = path.rename(path.with_name("job005--worker09.json"))
    old = time.time() - 3600
    os.utime(orphan, (old, old))
    # worker09 has no status file -> considered stale
    sys_argv = sys.argv
    sys.argv = ["requeue_failed.py", "--root", str(root), "--stale-running-sec", "600"]
    try:
        assert requeue_failed.main() == 0
    finally:
        sys.argv = sys_argv
    assert (root / "jobs" / "pending" / "job005.json").exists()


def test_requeue_stale_running_skips_alive_worker(root: Path) -> None:
    path = write_job(root, "job006", bucket="running")
    orphan = path.rename(path.with_name("job006--worker01.json"))
    old = time.time() - 3600
    os.utime(orphan, (old, old))
    worker.update_status(root, "worker01", "running", "job006")
    sys_argv = sys.argv
    sys.argv = ["requeue_failed.py", "--root", str(root), "--stale-running-sec", "600"]
    try:
        assert requeue_failed.main() == 0
    finally:
        sys.argv = sys_argv
    assert orphan.exists()
    assert not (root / "jobs" / "pending" / "job006.json").exists()


def test_render_template_only_dunder_keys() -> None:
    job = {"seed": 7, "case": "case001"}
    rendered = worker.render_template("run --seed __seed__ {case} {not_a_key}", job)
    assert rendered == "run --seed 7 {case} {not_a_key}"
