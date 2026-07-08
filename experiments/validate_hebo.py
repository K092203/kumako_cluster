#!/usr/bin/env python3
"""検証5: HEBOエンジン vs Optuna TPE既定値.

同一目的関数(mock_problem, 5seed平均)・同一trial数(40)・バッチ4のask/tellで比較。
サンプラー自体の計算時間(1repあたりのsuggestオーバーヘッド)も記録する。

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
import time
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
TRIALS = 40
BATCH = 4
REPS = 10


def evaluate(params) -> float:
    vals = []
    for seed in SPEC["instances"]:
        sites = M.make_instance(seed)
        chosen = M.solve(sites, int(params["iters"]), float(params["t0"]), float(params["cooling"]),
                         random.Random(seed + 12345))
        vals.append(M.score_selection(sites, chosen))
    return sum(vals) / len(vals)


def run_rep(job):
    arm, rep = job
    import optuna_bridge as ob

    tmp = Path(tempfile.mkdtemp())
    if arm == "hebo":
        eng = ob.HEBOEngine(SPEC, "max", tmp / "h.jsonl", seed=rep)
    else:
        import optuna

        optuna.logging.set_verbosity(optuna.logging.WARNING)
        eng = ob.OptunaEngine(SPEC, "max", tmp / "j.log", f"{arm}-{rep}")
    curve = []
    cur = None
    done = 0
    sampler_sec = 0.0
    while done < TRIALS:
        n = min(BATCH, TRIALS - done)
        t0 = time.perf_counter()
        asked = [eng.ask() for _ in range(n)]
        sampler_sec += time.perf_counter() - t0
        for ref, params in asked:
            v = evaluate(params)
            t0 = time.perf_counter()
            eng.tell(ref, params, v)
            sampler_sec += time.perf_counter() - t0
            cur = v if cur is None or v > cur else cur
            curve.append(cur)
        done += n
    return {"arm": arm, "rep": rep, "best20": curve[min(19, len(curve) - 1)], "best40": curve[-1],
            "sampler_sec": round(sampler_sec, 2)}


def boot_ci(diffs, n=10000, seed=0):
    rng = random.Random(seed)
    ms = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(n))
    return ms[int(0.025 * n)], ms[int(0.975 * n)]


def main():
    jobs = [(a, r) for a in ("tpe", "hebo") for r in range(REPS)]
    with Pool(2) as pool:
        rows = pool.map(run_rep, jobs)
    out = {"rows": rows}
    by = {a: sorted([r for r in rows if r["arm"] == a], key=lambda r: r["rep"]) for a in ("tpe", "hebo")}
    for k in ("best20", "best40"):
        t = [r[k] for r in by["tpe"]]
        h = [r[k] for r in by["hebo"]]
        diffs = [b - a for a, b in zip(t, h)]
        lo, hi = boot_ci(diffs)
        out[k] = {
            "tpe_mean": statistics.fmean(t), "tpe_sd": statistics.stdev(t),
            "hebo_mean": statistics.fmean(h), "hebo_sd": statistics.stdev(h),
            "diff_mean_hebo_minus_tpe": statistics.fmean(diffs), "diff_ci95": [lo, hi],
            "wins": sum(x > 0 for x in diffs), "losses": sum(x < 0 for x in diffs), "reps": REPS,
        }
        print(k, json.dumps(out[k]))
    out["sampler_sec"] = {a: statistics.fmean([r["sampler_sec"] for r in by[a]]) for a in by}
    print("sampler_sec", json.dumps(out["sampler_sec"]))
    (ROOT / "experiments" / "results_hebo.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
