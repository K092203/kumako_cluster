#!/usr/bin/env python3
"""検証3: successive halving (seed数=忠実度) vs 全seed一括評価.

同一の「seed評価回数」予算(400回)の下で、
 full   : 各trialを常に5seedで評価するTPE
 halving: HalvingScheduler (rungs=[1,2,5], eta=3) + TPE
を比較する。指標は「全5seedで評価済みの最良平均スコア」。
halving側は昇格時に既評価seedの結果を再利用する(本番の resume 機構と同じ)。

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

BUDGET = 400  # seed評価回数
REPS = 30


def eval_seed(params, seed) -> float:
    sites = M.make_instance(seed)
    chosen = M.solve(sites, int(params["iters"]), float(params["t0"]), float(params["cooling"]),
                     random.Random(seed + 12345))
    return M.score_selection(sites, chosen)


def best_at(curve, budget):
    vals = [b for e, b in curve if e <= budget]
    return max(vals) if vals else None


def run_rep(job):
    arm, rep = job
    import optuna
    import optuna_bridge as ob

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    tmp = Path(tempfile.mkdtemp())
    eng = ob.OptunaEngine(SPEC, "max", tmp / "j.log", f"{arm}-{rep}")
    evals = 0
    curve = []  # (evals_used, best_full_fidelity)

    if arm == "full":
        best = None
        while evals + 5 <= BUDGET:
            ref, params = eng.ask()
            scores = [eval_seed(params, s) for s in SPEC["instances"]]
            evals += 5
            v = statistics.fmean(scores)
            eng.tell(ref, params, v)
            if best is None or v > best:
                best = v
            curve.append((evals, best))
    else:
        sched = ob.HalvingScheduler(eng, len(SPEC["instances"]), eta=3, direction="max")
        cache: dict = {}

        def evaluate_rung(cands, n_seeds):
            nonlocal evals
            results = []
            for ref, params in cands:
                number = ref.number if hasattr(ref, "number") else ref
                vals = []
                for s in SPEC["instances"][:n_seeds]:
                    if (number, s) not in cache:
                        cache[(number, s)] = eval_seed(params, s)
                        evals += 1
                    vals.append(cache[(number, s)])
                results.append(statistics.fmean(vals))
            return results

        while evals < BUDGET:
            sched.run_bracket(evaluate_rung)
            curve.append((evals, sched.best["score"] if sched.best else None))

    return {"arm": arm, "rep": rep, "evals": evals,
            "best200": best_at(curve, 200), "best400": best_at(curve, max(BUDGET, evals))}


def boot_ci(diffs, n=10000, seed=0):
    rng = random.Random(seed)
    ms = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(n))
    return ms[int(0.025 * n)], ms[int(0.975 * n)]


def main():
    jobs = [(a, r) for a in ("full", "halving") for r in range(REPS)]
    with Pool(3) as pool:
        rows = pool.map(run_rep, jobs)
    out = {"rows": rows}
    by = {a: sorted([r for r in rows if r["arm"] == a], key=lambda r: r["rep"]) for a in ("full", "halving")}
    for k in ("best200", "best400"):
        f = [r[k] for r in by["full"]]
        h = [r[k] for r in by["halving"]]
        diffs = [b - a for a, b in zip(f, h)]
        lo, hi = boot_ci(diffs)
        out[k] = {
            "full_mean": statistics.fmean(f), "full_sd": statistics.stdev(f),
            "halving_mean": statistics.fmean(h), "halving_sd": statistics.stdev(h),
            "diff_mean_halving_minus_full": statistics.fmean(diffs), "diff_ci95": [lo, hi],
            "wins": sum(x > 0 for x in diffs), "losses": sum(x < 0 for x in diffs), "reps": REPS,
        }
        print(k, json.dumps(out[k]))
    (ROOT / "experiments" / "results_halving.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
