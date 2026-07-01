import json
import sys

from runledger import sweep as sweep_mod
from runledger.measure import MeasureSpec

PY = sys.executable

# A solver that reports a score via #TUNE, and fails (non-zero) when score < 0.
SOLVER = (
    "import sys, argparse\n"
    "ap = argparse.ArgumentParser()\n"
    "ap.add_argument('--score', type=float, default=1.0)\n"
    "a, _ = ap.parse_known_args()\n"
    "if a.score < 0:\n"
    "    sys.exit(2)\n"
    "print(f'#TUNE elapsed=0.01 score={a.score} correct=1', file=sys.stderr)\n"
)


def _solver(tmp_path):
    p = tmp_path / "solver.py"
    p.write_text(SOLVER)
    return p


def _configs(tmp_path, rows):
    lines = ["\t".join(["id", "target", "bin", "ranks", "omp", "rep", "args"])]
    lines += ["\t".join(r) for r in rows]
    p = tmp_path / "configs.tsv"
    p.write_text("\n".join(lines) + "\n")
    return p


def _run(tmp_path, cfg, **kw):
    return sweep_mod.run_sweep(
        cfg,
        base_command=[PY, "solver.py"],
        sweep_root=tmp_path / "sweeps",
        state_dir=tmp_path / "state",
        measure_spec=MeasureSpec(),
        cwd=tmp_path,
        **kw,
    )


def test_sweep_results_and_live_incumbent(tmp_path):
    _solver(tmp_path)
    cfg = _configs(
        tmp_path,
        [
            ("000", "solver", "solver", "1", "1", "1", "--score 5"),
            ("001", "solver", "solver", "1", "1", "1", "--score 20"),
            ("002", "solver", "solver", "1", "1", "1", "--score 9"),
        ],
    )
    res = _run(tmp_path, cfg, budget=5, elapse=60, objective="max-score")
    assert res.configs_run == 3
    rows = sweep_mod.read_results(res.sweep_dir)
    assert len(rows) == 3
    assert all(r.correct for r in rows)
    inc = json.loads((tmp_path / "state" / "incumbent.json").read_text())
    assert inc["id"] == "001"
    assert inc["score"] == 20.0


def test_sweep_continues_past_failure(tmp_path):
    _solver(tmp_path)
    cfg = _configs(
        tmp_path,
        [
            ("000", "solver", "solver", "1", "1", "1", "--score 5"),
            ("001", "solver", "solver", "1", "1", "1", "--score -1"),  # fails
            ("002", "solver", "solver", "1", "1", "1", "--score 9"),
        ],
    )
    res = _run(tmp_path, cfg, budget=5, elapse=60, objective="max-score")
    rows = {r.id: r for r in sweep_mod.read_results(res.sweep_dir)}
    assert len(rows) == 3
    assert rows["001"].correct is False  # failure recorded, not trusted
    assert rows["001"].score == 0.0
    # incumbent skipped the failure and picked the best correct config
    inc = json.loads((tmp_path / "state" / "incumbent.json").read_text())
    assert inc["id"] == "002"


def test_sweep_anytime_cutoff(tmp_path):
    _solver(tmp_path)
    cfg = _configs(
        tmp_path,
        [("000", "solver", "solver", "1", "1", "1", "--score 5")],
    )
    # budget huge vs tiny elapse -> cannot even start the first config
    res = _run(tmp_path, cfg, budget=100, elapse=0.001, objective="max-score")
    assert res.stopped_early is True
    assert res.configs_run == 0
    assert "anytime cutoff" in (res.sweep_dir / "errors.log").read_text()


def test_read_configs_detects_duplicate_ids(tmp_path):
    cfg = _configs(
        tmp_path,
        [
            ("000", "solver", "solver", "1", "1", "1", "--score 5"),
            ("000", "solver", "solver", "1", "1", "1", "--score 9"),
        ],
    )
    configs, warnings = sweep_mod.read_configs(cfg)
    assert len(configs) == 1
    assert any("duplicate" in w for w in warnings)
