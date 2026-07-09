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


def test_result_index_polls_done_failed_and_retries_missing(root: Path) -> None:
    job_id = "s1-t0000-i0001"
    index = bridge.ResultIndex(root)
    (root / "jobs" / "done" / f"{job_id}.json").write_text(json.dumps({"job_id": job_id}))
    result_dir = root / "results" / "w1" / job_id
    result_dir.mkdir(parents=True)
    result = {"outcome": "completed", "measure": {"score": 1.5, "correct": True}}
    (result_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")

    index.poll({job_id})
    assert index.get(job_id) == result

    renamed_id = "s1-t0000-i0002"
    (root / "jobs" / "done" / f"{renamed_id}-2.json").write_text(json.dumps({"job_id": renamed_id}))
    result_dir = root / "results" / "w1" / renamed_id
    result_dir.mkdir(parents=True)
    renamed_result = {"outcome": "completed", "measure": {"score": 2.0, "correct": True}}
    (result_dir / "result.json").write_text(json.dumps(renamed_result), encoding="utf-8")

    index.poll({renamed_id})
    assert index.get(renamed_id) == renamed_result

    late_id = "s1-t0000-i0003"
    (root / "jobs" / "failed" / f"{late_id}.json").write_text(json.dumps({"job_id": late_id}))
    index.poll({late_id})
    assert index.get(late_id) is None

    result_dir = root / "results" / "w2" / late_id
    result_dir.mkdir(parents=True)
    late_result = {"outcome": "failed", "measure": {"score": None, "correct": None}}
    (result_dir / "result.json").write_text(json.dumps(late_result), encoding="utf-8")
    index.poll({late_id})
    assert index.get(late_id) == late_result


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
            (r / "jobs" / "done" / f"{job_id}.json").write_text(json.dumps({"job_id": job_id}))
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


def test_warm_start_history_and_best_enqueue(tmp_path: Path) -> None:
    spec = {
        "name": "ws",
        "params": {
            "alpha": {"type": "float", "low": 0.0, "high": 1.0},
            "iters": {"type": "int", "low": 1, "high": 100},
            "strategy": {"type": "cat", "choices": ["a", "b"]},
        },
    }
    history = tmp_path / "old.history.jsonl"
    rows = [
        {"number": 0, "params": {"alpha": 0.2, "iters": 10, "strategy": "a"}, "score": 1.0},
        {"number": 1, "params": {"alpha": 2.0, "iters": 150, "strategy": "b"}, "score": 10.0},
        {"number": 2, "params": {"alpha": 0.4, "iters": 20, "strategy": "a"}, "score": 8.0},
        {"number": 3, "params": {"alpha": 0.5, "iters": 30, "strategy": "x"}, "score": 99.0},
        {"number": 4, "params": {"alpha": 0.6, "iters": 40, "strategy": "a"}, "score": None},
    ]
    history.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    engine = bridge.BuiltinEngine(spec, "max", tmp_path / "hist.jsonl", seed=1)
    queued = bridge.enqueue_warm_starts(engine, spec, [history], 2, "max")
    assert queued == 2
    assert engine.queue == [
        {"alpha": 1.0, "iters": 100, "strategy": "b"},
        {"alpha": 0.4, "iters": 20, "strategy": "a"},
    ]

    best = tmp_path / "old.best.json"
    best.write_text(json.dumps({"params": {"alpha": -1.0, "iters": 3.2, "strategy": "a"}}), encoding="utf-8")
    engine = bridge.BuiltinEngine(spec, "max", tmp_path / "hist2.jsonl", seed=1)
    queued = bridge.enqueue_warm_starts(engine, spec, [best], 5, "max")
    assert queued == 1
    assert engine.queue == [{"alpha": 0.0, "iters": 3, "strategy": "a"}]


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


def test_signed_rank_p_worse_selftest_cases() -> None:
    assert abs(bridge.signed_rank_p_worse([-1, -2, -3, -4]) - 0.0625) < 1e-12
    assert bridge.signed_rank_p_worse([1, 2, 3, 4]) > 0.999
    assert abs(bridge.signed_rank_p_worse([0, -1, 2]) - 0.75) < 1e-12
    assert abs(bridge.signed_rank_p_worse([0, -1, -2]) - 0.25) < 1e-12
    assert bridge.signed_rank_p_worse([0, 0, 0]) == 1.0


def _search_args(**overrides):
    args = dict(
        max_trials=2, parallel=1, agg="mean", direction="max",
        poll_sec=0, trial_timeout_sec=600, seed=1,
        race=False, race_p=0.05, race_startup=0,
        hedge=False, hedge_factor=2.0, hedge_min_wait_sec=30.0,
        hedge_remaining_frac=0.2, hedge_max_frac=0.10,
    )
    args.update(overrides)
    return types.SimpleNamespace(**args)


def _write_result(root: Path, job_id: str, score: float) -> None:
    result_dir = root / "results" / "w1" / job_id
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "result.json").write_text(
        json.dumps({"outcome": "completed", "measure": {"score": score, "correct": True}}),
        encoding="utf-8",
    )
    (root / "jobs" / "done" / f"{job_id}.json").write_text(json.dumps({"job_id": job_id}), encoding="utf-8")


def test_racing_prunes_bad_challenger_before_wave2(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = dict(SPEC, instances=[1, 2, 3, 4, 5, 6])
    engine = bridge.BuiltinEngine(spec, "max", root / "state" / "search" / "s1.history.jsonl", seed=3)
    real_emit = bridge.emit_trial_jobs

    def emit_and_answer(r, s, tag, params, seeds=None):
        job_ids = real_emit(r, s, tag, params, seeds=seeds)
        for job_id in job_ids:
            _write_result(r, job_id, 10.0 if tag.endswith("t0000") else 0.0)
        return job_ids

    monkeypatch.setattr(bridge, "emit_trial_jobs", emit_and_answer)
    monkeypatch.setattr(bridge.time, "sleep", lambda s: None)

    best = bridge.run_search(root, spec, engine, "builtin", _search_args(race=True, race_startup=5))
    assert best["score"] == 10.0
    assert engine.history[-1]["score"] == 0.0
    order = bridge.deterministic_race_order(spec["instances"], "s1-t0001")
    wave2_job = f"s1-t0001-i{order[-1]:04d}.json"
    assert not (root / "jobs" / "pending" / wave2_job).exists()


def test_racing_noop_submits_wave2_for_non_worse(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = dict(SPEC, instances=[1, 2, 3, 4, 5, 6])
    engine = bridge.BuiltinEngine(spec, "max", root / "state" / "search" / "s1.history.jsonl", seed=3)
    real_emit = bridge.emit_trial_jobs

    def emit_and_answer(r, s, tag, params, seeds=None):
        job_ids = real_emit(r, s, tag, params, seeds=seeds)
        for job_id in job_ids:
            _write_result(r, job_id, 10.0 if tag.endswith("t0000") else 12.0)
        return job_ids

    monkeypatch.setattr(bridge, "emit_trial_jobs", emit_and_answer)
    monkeypatch.setattr(bridge.time, "sleep", lambda s: None)

    best = bridge.run_search(root, spec, engine, "builtin", _search_args(race=True, race_startup=5))
    assert best["score"] == 12.0
    order = bridge.deterministic_race_order(spec["instances"], "s1-t0001")
    wave2_job = f"s1-t0001-i{order[-1]:04d}.json"
    assert (root / "jobs" / "pending" / wave2_job).exists()


def test_race_with_agg_max_warns_and_uses_full_submission(root: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    spec = dict(SPEC, instances=[1, 2, 3, 4, 5, 6])
    engine = bridge.BuiltinEngine(spec, "max", root / "state" / "search" / "s1.history.jsonl", seed=3)
    real_emit = bridge.emit_trial_jobs
    emitted_counts = []

    def emit_and_answer(r, s, tag, params, seeds=None):
        job_ids = real_emit(r, s, tag, params, seeds=seeds)
        emitted_counts.append(len(job_ids))
        for job_id in job_ids:
            _write_result(r, job_id, 1.0)
        return job_ids

    monkeypatch.setattr(bridge, "emit_trial_jobs", emit_and_answer)
    monkeypatch.setattr(bridge.time, "sleep", lambda s: None)

    bridge.run_search(root, spec, engine, "builtin", _search_args(max_trials=1, race=True, agg="max", race_startup=5))
    assert "disabling racing" in capsys.readouterr().out
    assert emitted_counts == [6]


def test_hedge_decision_threshold_once_and_breaker(root: Path) -> None:
    spec = dict(SPEC, instances=[1, 2, 3, 4, 5])
    state = {
        "params": {"alpha": 0.1},
        "submitted_job_ids": [f"s1-t0000-i{i:04d}" for i in range(1, 6)],
        "resolved_jobs": {f"s1-t0000-i{i:04d}" for i in range(1, 5)},
        "durations": [9.0, 10.0, 11.0],
        "hedged_jobs": set(),
        "job_started_at": {"s1-t0000-i0005": 0.0},
        "seed_by_job": {f"s1-t0000-i{i:04d}": i for i in range(1, 6)},
        "aliases": {},
        "alias_to_original": {},
        "session_jobs": set(),
    }
    args = _search_args(hedge=True, hedge_factor=2.0, hedge_min_wait_sec=30.0)
    stats = {"submitted": 5, "duplicates": 0, "disabled": False, "notified": False}

    bridge.hedge_missing_jobs(root, spec, "s1-t0000", state, args, 49.0, stats)
    assert not list((root / "jobs" / "pending").glob("*-h1.json"))

    bridge.hedge_missing_jobs(root, spec, "s1-t0000", state, args, 51.0, stats)
    assert (root / "jobs" / "pending" / "s1-t0000-i0005-h1.json").exists()
    bridge.hedge_missing_jobs(root, spec, "s1-t0000", state, args, 100.0, stats)
    assert len(list((root / "jobs" / "pending").glob("*-h1.json"))) == 1

    state["hedged_jobs"].clear()
    state["aliases"].clear()
    state["alias_to_original"].clear()
    stats = {"submitted": 10, "duplicates": 2, "disabled": False, "notified": False}
    bridge.hedge_missing_jobs(root, spec, "s1-t0000", state, args, 100.0, stats)
    assert stats["disabled"] is True


def test_hedge_integration_completes_with_duplicate_result(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = dict(SPEC, instances=[1, 2, 3, 4, 5])
    engine = bridge.BuiltinEngine(spec, "max", root / "state" / "search" / "s1.history.jsonl", seed=3)

    class Clock:
        now = 0.0
        sleeps = 0

    def fake_sleep(seconds):
        Clock.sleeps += 1
        Clock.now += 100.0
        pending = sorted((root / "jobs" / "pending").glob("*.json"))
        if Clock.sleeps == 1:
            for path in pending[:4]:
                _write_result(root, path.stem, 1.0)
        else:
            for path in pending:
                if path.stem.endswith("-h1"):
                    _write_result(root, path.stem, 5.0)

    monkeypatch.setattr(bridge.time, "time", lambda: Clock.now)
    monkeypatch.setattr(bridge.time, "sleep", fake_sleep)

    best = bridge.run_search(
        root, spec, engine, "builtin",
        _search_args(max_trials=1, hedge=True, hedge_factor=0.0, hedge_min_wait_sec=1.0),
    )
    assert (root / "jobs" / "pending" / "s1-t0000-i0005-h1.json").exists()
    assert best["score"] == pytest.approx(1.8)
