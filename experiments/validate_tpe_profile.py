#!/usr/bin/env python3
"""検証1: TPE推奨設定 (multivariate+constant_liar) vs Optuna既定値.

mock_problem の焼きなましソルバー(5インスタンスseedの平均スコア)を目的関数に、
実運用のブリッジと同じ「バッチ8のask/tell」で60trial探索する。
各armを同一rep seedで対にし、best@30 / best@60 を比較する。

注: itersの上限は600に制限している。上限20000では中央値のランダム設定でも
最適値72.767に到達して目的関数が飽和し、手法間の差が測定不能になることを
事前実験で確認済み(本番も制限時間でitersは固定に近く、t0/coolingの調整が主戦場)。
"""

from __future__ import annotations

import importlib.util
import json
import random
import statistics
import sys
import tempfile
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

_s = importlib.util.spec_from_file_location("mock_solver", ROOT / "examples/mock_problem/solver.py")
M = importlib.util.module_from_spec(_s)
_s.loader.exec_module(M)

SPEC = {
    "name": "mock",
    "params": {
        "iters": {"type": "int", "low": 100, "high": 600, "log": True},
        "t0": {"type": "float", "low": 0.1, "high": 50.0, "log": True},
        "cooling": {"type": "float", "low": 0.0001, "high": 0.5, "log": True},
    },
    "command": ["x"],
    "instances": [1, 2, 3, 4, 5],
    "timeout_sec": 60,
}

TRIALS = 60
BATCH = 8
REPS = 30


def evaluate(params) -> float:
    vals = []
    for seed in SPEC["instances"]:
        sites = M.make_instance(seed)
        chosen = M.solve(sites, int(params["iters"]), float(params["t0"]), float(params["cooling"]),
                         random.Random(seed + 12345))
        vals.append(M.score_selection(sites, chosen))
    return sum(vals) / len(vals)


def run_rep(job):
    profile, rep = job
    import optuna
    import optuna_bridge as ob

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    tmp = Path(tempfile.mkdtemp())
    eng = ob.OptunaEngine(SPEC, "max", tmp / "j.log", f"{profile}-{rep}", tpe_profile=profile, seed=rep)
    curve = []
    cur = None
    done = 0
    while done < TRIALS:
        n = min(BATCH, TRIALS - done)
        asked = [eng.ask() for _ in range(n)]
        for ref, params in asked:
            v = evaluate(params)
            eng.tell(ref, params, v)
            cur = v if cur is None or v > cur else cur
            curve.append(cur)
        done += n
    return {"profile": profile, "rep": rep, "best30": curve[min(29, len(curve) - 1)], "best60": curve[-1]}


def boot_ci(diffs, n=10000, seed=0):
    rng = random.Random(seed)
    ms = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(n))
    return ms[int(0.025 * n)], ms[int(0.975 * n)]


def main():
    jobs = [(p, r) for p in ("default", "recommended") for r in range(REPS)]
    with Pool(3) as pool:
        rows = pool.map(run_rep, jobs)
    out = {"rows": rows}
    by = {p: sorted([r for r in rows if r["profile"] == p], key=lambda r: r["rep"]) for p in ("default", "recommended")}
    for k in ("best30", "best60"):
        d = [r[k] for r in by["default"]]
        rec = [r[k] for r in by["recommended"]]
        diffs = [b - a for a, b in zip(d, rec)]
        lo, hi = boot_ci(diffs)
        out[k] = {
            "default_mean": statistics.fmean(d), "default_sd": statistics.stdev(d),
            "recommended_mean": statistics.fmean(rec), "recommended_sd": statistics.stdev(rec),
            "paired_diff_mean": statistics.fmean(diffs), "paired_diff_ci95": [lo, hi],
            "wins": sum(x > 0 for x in diffs), "losses": sum(x < 0 for x in diffs), "reps": REPS,
        }
        print(k, json.dumps(out[k]))
    (ROOT / "experiments" / "results_tpe_profile.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
