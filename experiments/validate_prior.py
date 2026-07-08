#!/usr/bin/env python3
"""検証4: 事前分布注入 (πBO方式) の効果と誤った事前分布への頑健性.

手順:
 1) 一様ランダム探索200点(実評価)で目的関数の良い領域/悪い領域を実測し、
    good prior(最良点中心) / bad prior(最悪点中心) を作る。
 2) TPE単体 / good prior / bad prior の3armを各12rep、30trialで比較。
    注入は本番コード maybe_enqueue_prior (β/(β+t)減衰) をそのまま使う。
指標: best@10(立ち上がり), best@30(頑健性)。

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
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

_s = importlib.util.spec_from_file_location("mock_solver", ROOT / "examples/mock_problem/solver.py")
M = importlib.util.module_from_spec(_s)
_s.loader.exec_module(M)

BASE_PARAMS = {
    "iters": {"type": "int", "low": 100, "high": 600, "log": True},
    "t0": {"type": "float", "low": 0.1, "high": 50.0, "log": True},
    "cooling": {"type": "float", "low": 0.0001, "high": 0.5, "log": True},
}
INSTANCES = [1, 2, 3, 4, 5]
TRIALS = 30
REPS = 24
CONF = 0.9


def evaluate(params) -> float:
    vals = []
    for seed in INSTANCES:
        sites = M.make_instance(seed)
        chosen = M.solve(sites, int(params["iters"]), float(params["t0"]), float(params["cooling"]),
                         random.Random(seed + 12345))
        vals.append(M.score_selection(sites, chosen))
    return sum(vals) / len(vals)


def spec_with_prior(center: dict | None) -> dict:
    import copy

    params = copy.deepcopy(BASE_PARAMS)
    if center:
        for name in params:
            params[name]["prior"] = {"center": center[name], "confidence": CONF}
    return {"name": "mock", "params": params, "command": ["x"], "instances": INSTANCES, "timeout_sec": 60}


def run_rep(job):
    arm, rep, center = job
    import optuna
    import optuna_bridge as ob

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    spec = spec_with_prior(center)
    args = SimpleNamespace(use_prior=center is not None, prior_p0=0.9, prior_beta=TRIALS / 4)
    prior_rng = random.Random(1000 + rep)
    eng = ob.OptunaEngine(spec, "max", Path(tempfile.mkdtemp()) / "j.log", f"{arm}-{rep}")
    best = None
    curve = []
    for t in range(TRIALS):
        ob.maybe_enqueue_prior(eng, spec, t, args, prior_rng)
        ref, params = eng.ask()
        v = evaluate(params)
        eng.tell(ref, params, v)
        if best is None or v > best:
            best = v
        curve.append(best)
    return {"arm": arm, "rep": rep, "best10": curve[min(9, len(curve) - 1)], "best30": curve[-1]}


def boot_ci(diffs, n=10000, seed=0):
    rng = random.Random(seed)
    ms = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(n))
    return ms[int(0.025 * n)], ms[int(0.975 * n)]


def main():
    rng = random.Random(0)
    probes = [(ob_params, evaluate(ob_params)) for ob_params in
              (sample(rng) for _ in range(200))]
    probes.sort(key=lambda kv: kv[1])
    bad_center, bad_val = probes[0]
    good_center, good_val = probes[-1]
    out = {"probe_good": {"params": good_center, "score": good_val},
           "probe_bad": {"params": bad_center, "score": bad_val},
           "probe_n": len(probes)}
    print("probe", json.dumps(out))

    jobs = ([("none", r, None) for r in range(REPS)]
            + [("good", r, good_center) for r in range(REPS)]
            + [("bad", r, bad_center) for r in range(REPS)])
    with Pool(3) as pool:
        rows = pool.map(run_rep, jobs)
    out["rows"] = rows
    by = {a: [r for r in rows if r["arm"] == a] for a in ("none", "good", "bad")}
    for k in ("best10", "best30"):
        stats = {}
        for a in by:
            vals = [r[k] for r in by[a]]
            stats[a] = {"mean": statistics.fmean(vals), "sd": statistics.stdev(vals)}
        for a in ("good", "bad"):
            diffs = [x[k] - y[k] for x, y in zip(by[a], by["none"])]
            lo, hi = boot_ci(diffs)
            stats[a]["diff_vs_none"] = statistics.fmean(diffs)
            stats[a]["diff_ci95"] = [lo, hi]
        out[k] = stats
        print(k, json.dumps(stats))
    (ROOT / "experiments" / "results_prior.json").write_text(json.dumps(out, indent=1))


def sample(rng):
    import optuna_bridge as ob

    return ob.sample_random(BASE_PARAMS, rng)


if __name__ == "__main__":
    main()
