"""Tests for parameter sweeps: make_job --param, sweep_id, and by-sweep aggregation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import make_job
import summarize_results
import worker


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    worker.ensure_layout(tmp_path)
    return tmp_path


def read_pending(root: Path) -> list[dict]:
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((root / "jobs" / "pending").glob("*.json"))
    ]


def write_result(root: Path, worker_id: str, job_id: str, sweep_id: str | None, score: float | None, params: dict | None = None, outcome: str = "completed") -> None:
    result_dir = root / "results" / worker_id / job_id
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "result.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "worker": worker_id,
                "sweep_id": sweep_id,
                "params": params,
                "outcome": outcome,
                "measure": {"score": score, "elapsed": 1.0, "correct": True},
            }
        ),
        encoding="utf-8",
    )


def test_make_job_params_and_deterministic_sweep_id(root: Path) -> None:
    make_job.main(["--root", str(root), "--count", "2", "--param", "alpha=0.5", "--param", "beta=2"])
    jobs = read_pending(root)
    assert len(jobs) == 2
    assert jobs[0]["params"] == {"alpha": 0.5, "beta": 2}
    assert jobs[0]["sweep_id"] == jobs[1]["sweep_id"]

    sweep = jobs[0]["sweep_id"]
    assert make_job.sweep_id_for({"beta": 2, "alpha": 0.5}) == sweep
    assert make_job.sweep_id_for({"alpha": 0.6, "beta": 2}) != sweep


def test_render_template_uses_params(root: Path) -> None:
    job = {"seed": 3, "params": {"alpha": 0.5}}
    assert worker.render_template("--alpha __alpha__ --seed __seed__", job) == "--alpha 0.5 --seed 3"


def test_result_json_carries_sweep_metadata(root: Path) -> None:
    path = root / "jobs" / "pending" / "job001.json"
    path.write_text(
        json.dumps(
            {
                "job_id": "job001",
                "command": "dummy",
                "elapsed": 0.0,
                "params": {"alpha": 0.5},
                "sweep_id": "abc123",
            }
        ),
        encoding="utf-8",
    )
    claimed = worker.claim_job(root, "worker01")
    worker.process_job(root, "worker01", root / "worker_local" / "worker01", claimed)
    result = json.loads((root / "results" / "worker01" / "job001" / "result.json").read_text())
    assert result["sweep_id"] == "abc123"
    assert result["params"] == {"alpha": 0.5}


def test_by_sweep_aggregation_mean_vs_min(root: Path, capsys: pytest.CaptureFixture) -> None:
    # sweep A: [4, 4] -> mean 4, min 4 / sweep B: [10, 1] -> mean 5.5, min 1
    write_result(root, "w1", "a1", "sweepA", 4.0, {"alpha": 1})
    write_result(root, "w2", "a2", "sweepA", 4.0, {"alpha": 1})
    write_result(root, "w1", "b1", "sweepB", 10.0, {"alpha": 2})
    write_result(root, "w2", "b2", "sweepB", 1.0, {"alpha": 2})
    write_result(root, "w1", "c1", None, 99.0)  # sweep_idなしは集計対象外

    summarize_results.main(["--root", str(root), "--by-sweep", "--agg", "mean", "--update-incumbent"])
    out = capsys.readouterr().out
    assert "best sweep (mean max-score): sweepB" in out
    incumbent = json.loads((root / "state" / "incumbent.json").read_text())
    assert incumbent["sweep_id"] == "sweepB"
    assert incumbent["score"] == 5.5
    assert incumbent["params"] == {"alpha": 2}

    summarize_results.main(["--root", str(root), "--by-sweep", "--agg", "min", "--update-incumbent"])
    out = capsys.readouterr().out
    assert "best sweep (min max-score): sweepA" in out
    incumbent = json.loads((root / "state" / "incumbent.json").read_text())
    assert incumbent["sweep_id"] == "sweepA"
    assert incumbent["score"] == 4.0


def test_by_sweep_ignores_failed_jobs(root: Path, capsys: pytest.CaptureFixture) -> None:
    write_result(root, "w1", "a1", "sweepA", 4.0)
    write_result(root, "w1", "a2", "sweepA", None, outcome="failed")
    summarize_results.main(["--root", str(root), "--by-sweep"])
    out = capsys.readouterr().out
    assert "sweepA" in out
    assert "best sweep (mean max-score): sweepA" in out
    line = next(l for l in out.splitlines() if l.startswith("sweepA"))
    fields = line.split()
    assert fields[1] == "2" and fields[2] == "1"  # n=2, ok=1


def test_iqm_and_bootstrap_ci():
    from summarize_results import iqm, stratified_bootstrap_ci

    assert iqm([1.0, 2.0, 3.0, 4.0, 100.0]) == 3.0  # 上下25%を捨てた中央3値の平均
    assert iqm([5.0, 7.0]) == 6.0  # n<4 は全体平均
    lo, hi = stratified_bootstrap_ci([[1.0, 1.1], [2.0, 2.1]], iqm, n_boot=500, seed=1)
    assert lo is not None and lo <= hi
    assert stratified_bootstrap_ci([], iqm) == (None, None)
