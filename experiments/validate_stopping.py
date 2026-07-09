#!/usr/bin/env python3
"""検証E4: プラトー自動終了則のオフライン評価.

TPE推奨設定で200trialのbest-so-far曲線をrepごとに記録し、追加探索なしで
patienceとeps_relの停止則を適用して節約率と最終bestからの損失を測る。
"""

from __future__ import annotations

import argparse
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

TRIALS = 200
BATCH = 8
REPS = 30
POOL_PROCESSES = 10
RULES = [(20, 0.0), (20, 1e-4), (30, 0.0), (30, 1e-4), (50, 0.0), (50, 1e-4)]


def init_worker(trials: int) -> None:
    global TRIALS
    TRIALS = trials


def evaluate(params, instances) -> float:
    vals = []
    for seed in instances:
        sites = M.make_instance(seed)
        chosen = M.solve(sites, int(params["iters"]), float(params["t0"]), float(params["cooling"]),
                         random.Random(seed + 12345))
        vals.append(M.score_selection(sites, chosen))
    return sum(vals) / len(vals)


def run_rep(rep: int) -> dict:
    import optuna
    import optuna_bridge as ob

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    tmp = Path(tempfile.mkdtemp())
    eng = ob.OptunaEngine(SPEC, "max", tmp / "j.log", f"stopping-{rep}", tpe_profile="recommended", seed=rep)
    curve = []
    best = None
    done = 0
    while done < TRIALS:
        n = min(BATCH, TRIALS - done)
        asked = [eng.ask() for _ in range(n)]
        for ref, params in asked:
            value = evaluate(params, SPEC["instances"])
            eng.tell(ref, params, value)
            best = value if best is None or value > best else best
            curve.append(best)
        done += n
    return {"rep": rep, "curve": curve, "final_best": curve[-1]}


def stop_trial(curve: list, patience: int, eps_rel: float) -> int:
    for t in range(patience, len(curve) + 1):
        old = curve[t - 1 - patience]
        new = curve[t - 1]
        if new - old <= eps_rel * max(1e-12, abs(old)):
            return t
    return len(curve)


def percentile(vals: list, pct: float) -> float:
    if not vals:
        return 0.0
    xs = sorted(vals)
    idx = int((len(xs) - 1) * pct)
    return xs[idx]


def summarize(rows: list, reps: int) -> dict:
    summary = {"reps": reps, "trials": TRIALS, "batch": BATCH, "rules": {}}
    for patience, eps_rel in RULES:
        per_rep = []
        for r in rows:
            stop = stop_trial(r["curve"], patience, eps_rel)
            saved_pct = (TRIALS - stop) / TRIALS * 100.0
            loss = r["curve"][-1] - r["curve"][stop - 1]
            per_rep.append({
                "rep": r["rep"],
                "stop_trial": stop,
                "saved_pct": saved_pct,
                "loss": loss,
                "stopped": stop < TRIALS,
                "success": loss <= 0.01 and saved_pct >= 30.0,
            })
        saved = [r["saved_pct"] for r in per_rep]
        losses = [r["loss"] for r in per_rep]
        key = f"W{patience}_eps{eps_rel:g}"
        entry = {
            "patience": patience,
            "eps_rel": eps_rel,
            "saved_pct_median": statistics.median(saved),
            "saved_pct_mean": statistics.fmean(saved),
            "loss_mean": statistics.fmean(losses),
            "loss_p95": percentile(losses, 0.95),
            "success_rate": sum(r["success"] for r in per_rep) / len(per_rep),
            "stop_rate": sum(r["stopped"] for r in per_rep) / len(per_rep),
            "per_rep": per_rep,
        }
        summary["rules"][key] = entry
        print(key, json.dumps({k: v for k, v in entry.items() if k != "per_rep"}, ensure_ascii=False))
    return summary


def main():
    parser = argparse.ArgumentParser(description="Offline validation for plateau stopping rules.")
    parser.add_argument("--quick", action="store_true", help="縮小版(REPSとtrial数を約1/6)")
    args = parser.parse_args()

    global TRIALS, REPS
    if args.quick:
        TRIALS = 34
        REPS = 5

    with Pool(POOL_PROCESSES, initializer=init_worker, initargs=(TRIALS,)) as pool:
        rows = pool.map(run_rep, list(range(REPS)))
    out = {"rows": rows, "summary": summarize(rows, REPS)}
    (ROOT / "experiments" / "results_stopping.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
