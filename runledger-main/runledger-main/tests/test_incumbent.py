from runledger.incumbent import (
    Candidate,
    compute_best,
    load_incumbent,
    objective_value,
    update_incumbent,
)


def make(cid, score=None, elapsed=None, correct=True):
    return Candidate(
        id=cid,
        target="solver",
        bin="solver",
        args="--x 1",
        elapsed=elapsed,
        score=score,
        correct=correct,
        source_sweep="round-001",
        source_run=f"{cid}_rep001",
    )


def test_objective_value():
    assert objective_value(None, 5.0, "max-score") == 5.0
    assert objective_value(2.0, None, "min-elapsed") == -2.0
    assert objective_value(2.0, 10.0, "score-per-sec") == 5.0


def test_update_improves_only(tmp_path):
    assert update_incumbent(tmp_path, make("000", score=10), objective="max-score") is True
    assert update_incumbent(tmp_path, make("001", score=5), objective="max-score") is False
    assert update_incumbent(tmp_path, make("002", score=20), objective="max-score") is True
    cur = load_incumbent(tmp_path)
    assert cur["id"] == "002"
    assert cur["score"] == 20


def test_incorrect_never_adopted(tmp_path):
    assert update_incumbent(tmp_path, make("000", score=100, correct=False), objective="max-score") is False
    assert load_incumbent(tmp_path) is None


def test_min_elapsed_objective(tmp_path):
    assert update_incumbent(tmp_path, make("000", elapsed=5.0, score=1), objective="min-elapsed") is True
    assert update_incumbent(tmp_path, make("001", elapsed=3.0, score=1), objective="min-elapsed") is True
    assert update_incumbent(tmp_path, make("002", elapsed=8.0, score=1), objective="min-elapsed") is False
    assert load_incumbent(tmp_path)["id"] == "001"


def test_compute_best_skips_incorrect(tmp_path):
    cands = [make("000", score=1), make("001", score=9, correct=False), make("002", score=5)]
    best = compute_best(cands, objective="max-score")
    assert best.id == "002"


def test_no_regression_on_rerun(tmp_path):
    update_incumbent(tmp_path, make("000", score=50), objective="max-score")
    # a fresh worse candidate must not overwrite the incumbent
    update_incumbent(tmp_path, make("001", score=10), objective="max-score")
    assert load_incumbent(tmp_path)["score"] == 50
