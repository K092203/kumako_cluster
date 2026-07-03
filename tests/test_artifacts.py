"""Tests for artifact collection."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import worker


WRITE_SOLUTION = (
    "import pathlib; "
    "pathlib.Path('out').mkdir(exist_ok=True); "
    "pathlib.Path('out/solution.txt').write_text('answer=42')"
)


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    worker.ensure_layout(tmp_path)
    return tmp_path


def write_job(root: Path, job_id: str, **extra) -> Path:
    path = root / "jobs" / "pending" / f"{job_id}.json"
    path.write_text(json.dumps({"job_id": job_id, **extra}), encoding="utf-8")
    return path


def run_one(root: Path, worker_id: str = "worker01") -> None:
    claimed = worker.claim_job(root, worker_id)
    assert claimed is not None
    worker.process_job(root, worker_id, root / "worker_local" / worker_id, claimed)


def read_result(root: Path, job_id: str, worker_id: str = "worker01") -> dict:
    return json.loads(
        (root / "results" / worker_id / job_id / "result.json").read_text()
    )


def test_artifacts_collected(root: Path) -> None:
    write_job(
        root,
        "job001",
        command=[sys.executable, "-c", WRITE_SOLUTION],
        timeout_sec=30,
        artifacts=["out/solution.txt"],
    )
    run_one(root)
    result = read_result(root, "job001")
    assert result["outcome"] == "completed"
    assert result["artifacts"] == {"collected": ["out/solution.txt"], "missing": []}
    copied = root / "results" / "worker01" / "job001" / "artifacts" / "out" / "solution.txt"
    assert copied.read_text() == "answer=42"


def test_artifacts_missing_recorded(root: Path) -> None:
    write_job(
        root,
        "job001",
        command=[sys.executable, "-c", "print('no output files')"],
        timeout_sec=30,
        artifacts=["out/*.csv"],
    )
    run_one(root)
    result = read_result(root, "job001")
    assert result["outcome"] == "completed"
    assert result["artifacts"] == {"collected": [], "missing": ["out/*.csv"]}


def test_stale_artifact_from_previous_job_not_collected(root: Path) -> None:
    write_job(
        root,
        "job001",
        command=[sys.executable, "-c", WRITE_SOLUTION],
        timeout_sec=30,
        artifacts=["out/solution.txt"],
    )
    run_one(root)

    # 同じワーカーで repo/ が使い回されても、前ジョブの解が混入しないこと
    write_job(
        root,
        "job002",
        command=[sys.executable, "-c", "pass"],
        timeout_sec=30,
        artifacts=["out/solution.txt"],
    )
    run_one(root)
    result = read_result(root, "job002")
    assert result["artifacts"] == {"collected": [], "missing": ["out/solution.txt"]}
    assert not (root / "results" / "worker01" / "job002" / "artifacts").exists()


def test_unsafe_artifact_patterns_ignored(root: Path) -> None:
    write_job(
        root,
        "job001",
        command="dummy",
        elapsed=0.0,
        artifacts=["../escape.txt", "/absolute.txt"],
    )
    run_one(root)
    result = read_result(root, "job001")
    assert result["artifacts"] == {"collected": [], "missing": []}
