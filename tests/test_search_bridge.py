"""Tests for the search bridge: spec parsing, builtin engine, job emission, optuna smoke."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import optuna_bridge as bridge
import worker


SPEC = {
    "name": "s1",
    "params": {
        "alpha": {"type": "float", "low": 0.0, "high": 1.0},
        "iters": {"type": "int", "low": 1, "high": 100},
        "batch": {"type": "int", "low": 10, "high": 10000, "log": True},
        "strategy": {"type": "cat", "choices": ["a", "b"]},
    },
    "command": ["solver", "--alpha", "__alpha__", "--seed", "__seed__"],
    "instances": [1, 2],
    "timeout_sec": 30,
}


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    worker.ensure_layout(tmp_path)
    return tmp_path


def test_load_spec_validates(tmp_path: Path) -> None:
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(SPEC), encoding="utf-8")
    spec = bridge.load_spec(path)
    assert spec["name"] == "s1"

    bad = dict(SPEC, params={"x": {"type": "float", "low": 2, "high": 1}})
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(SystemExit):
        bridge.load_spec(path)

    bad = dict(SPEC, instances=[])
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(SystemExit):
        bridge.load_spec(path)


def test_sample_and_perturb_respect_bounds() -> None:
    import random

    rng = random.Random(1)
    for _ in range(200):
        params = bridge.sample_random(SPEC["params"], rng)
        assert 0.0 <= params["alpha"] <= 1.0
        assert 1 <= params["iters"] <= 100
        assert params["strategy"] in ("a", "b")
        # logスケールのintもint型で境界内であること(E2Eで発覚したバグの回帰テスト)
        assert isinstance(params["batch"], int) and 10 <= params["batch"] <= 10000
        shifted = bridge.perturb(SPEC["params"], params, rng)
        assert 0.0 <= shifted["alpha"] <= 1.0
        assert 1 <= shifted["iters"] <= 100
        assert isinstance(shifted["iters"], int)
        assert isinstance(shifted["batch"], int) and 10 <= shifted["batch"] <= 10000


def test_builtin_engine_improves_on_quadratic(tmp_path: Path) -> None:
    spec = {"params": {"alpha": {"type": "float", "low": 0.0, "high": 1.0}}}
    engine = bridge.BuiltinEngine(spec, "max", tmp_path / "hist.jsonl", seed=42)

    def objective(params: dict) -> float:
        return -((params["alpha"] - 0.3) ** 2)

    for _ in range(60):
        ref, params = engine.ask()
        engine.tell(ref, params, objective(params))

    best = engine.best()
    assert best is not None
    assert abs(best["params"]["alpha"] - 0.3) < 0.1

    # resume: 履歴を読み直しても現職と試行番号が引き継がれる
    resumed = bridge.BuiltinEngine(spec, "max", tmp_path / "hist.jsonl", seed=1)
    assert resumed.best()["score"] == best["score"]
    assert resumed.next_number == 60


def test_emit_and_collect_trial_jobs(root: Path) -> None:
    params = {"alpha": 0.5, "iters": 10, "strategy": "a"}
    job_ids = bridge.emit_trial_jobs(root, SPEC, "s1-t0000", params)
    assert job_ids == ["s1-t0000-i0001", "s1-t0000-i0002"]
    pending = sorted((root / "jobs" / "pending").glob("*.json"))
    assert len(pending) == 2
    job = json.loads(pending[0].read_text())
    assert job["sweep_id"] == "s1-t0000"
    assert job["params"] == params
    assert job["seed"] == 1

    scores, failed, missing = bridge.collect_scores(root, job_ids)
    assert (scores, failed, missing) == ([], 0, 2)

    result_dir = root / "results" / "w1" / "s1-t0000-i0001"
    result_dir.mkdir(parents=True)
    (result_dir / "result.json").write_text(
        json.dumps({"outcome": "completed", "measure": {"score": 7.0, "correct": True}})
    )
    scores, failed, missing = bridge.collect_scores(root, job_ids)
    assert (scores, failed, missing) == ([7.0], 0, 1)

    # 結果が既にあるジョブは再投入されない(resume)
    for path in pending:
        path.unlink()
    job_ids2 = bridge.emit_trial_jobs(root, SPEC, "s1-t0000", params)
    assert job_ids2 == job_ids
    assert len(list((root / "jobs" / "pending").glob("*.json"))) == 1


def test_run_search_loop_with_fake_results(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = dict(SPEC, instances=[1])
    engine = bridge.BuiltinEngine(spec, "max", root / "state" / "search" / "s1.history.jsonl", seed=7)

    # ジョブ投入直後に結果を偽装する(クラスタなしでループを回す)
    real_emit = bridge.emit_trial_jobs

    def emit_and_answer(r, s, tag, params):
        job_ids = real_emit(r, s, tag, params)
        for job_id in job_ids:
            result_dir = r / "results" / "w1" / job_id
            result_dir.mkdir(parents=True, exist_ok=True)
            (result_dir / "result.json").write_text(
                json.dumps(
                    {
                        "outcome": "completed",
                        "measure": {"score": -((params["alpha"] - 0.3) ** 2), "correct": True},
                    }
                )
            )
        return job_ids

    monkeypatch.setattr(bridge, "emit_trial_jobs", emit_and_answer)
    monkeypatch.setattr(bridge.time, "sleep", lambda s: None)

    args = types.SimpleNamespace(
        max_trials=20, parallel=4, agg="mean", direction="max",
        poll_sec=0, trial_timeout_sec=60, seed=7,
    )
    best = bridge.run_search(root, spec, engine, "builtin", args)
    assert best is not None
    assert best["score"] <= 0
    best_file = json.loads((root / "state" / "search" / "s1.best.json").read_text())
    assert best_file["params"] == best["params"]


def test_optuna_engine_smoke(tmp_path: Path) -> None:
    pytest.importorskip("optuna")
    spec = {"params": {"alpha": {"type": "float", "low": 0.0, "high": 1.0}}, "name": "sm"}
    engine = bridge.OptunaEngine(spec, "max", tmp_path / "journal.log", "sm")
    for _ in range(5):
        ref, params = engine.ask()
        assert 0.0 <= params["alpha"] <= 1.0
        engine.tell(ref, params, -((params["alpha"] - 0.3) ** 2))
    best = engine.best()
    assert best is not None and best["score"] <= 0

    failed_ref, params = engine.ask()
    engine.tell(failed_ref, params, None)  # FAIL扱いで例外にならない
    assert len(engine.study.trials) == 6
