#!/usr/bin/env python3
"""検証E3: 前夜studyからのウォームスタート.

sourceインスタンスで得た上位5個のparamsを、分布シフトしたtargetインスタンスの
翌日探索にenqueueし、cold startと同一repで対比較する。
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

SOURCE_TRIALS = 60
TARGET_TRIALS = 30
BATCH = 4
REPS = 30
POOL_PROCESSES = 10
SOURCE_INSTANCES = [1, 2, 3, 4, 5]
TARGET_INSTANCES = [11, 12, 13, 14, 15]


def init_worker(source_trials: int, target_trials: int) -> None:
    global SOURCE_TRIALS, TARGET_TRIALS
    SOURCE_TRIALS = source_trials
    TARGET_TRIALS = target_trials


def evaluate(params, instances) -> float:
    vals = []
    for seed in instances:
        sites = M.make_instance(seed)
        chosen = M.solve(sites, int(params["iters"]), float(params["t0"]), float(params["cooling"]),
                         random.Random(seed + 12345))
        vals.append(M.score_selection(sites, chosen))
    return sum(vals) / len(vals)


def run_engine(name: str, rep: int, seed: int, trials: int, batch: int, instances, enqueued=None) -> tuple:
    import optuna
    import optuna_bridge as ob

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    tmp = Path(tempfile.mkdtemp())
    eng = ob.OptunaEngine(SPEC, "max", tmp / "j.log", f"{name}-{rep}", tpe_profile="recommended", seed=seed)
    for params in enqueued or []:
        eng.enqueue(params)
    curve = []
    history = []
    best = None
    done = 0
    while done < trials:
        n = min(batch, trials - done)
        asked = [eng.ask() for _ in range(n)]
        for ref, params in asked:
            value = evaluate(params, instances)
            eng.tell(ref, params, value)
            history.append({"params": params, "value": value})
            best = value if best is None or value > best else best
            curve.append(best)
        done += n
    return curve, history


def run_rep(rep: int) -> dict:
    source_curve, source_history = run_engine("source", rep, rep, SOURCE_TRIALS, BATCH, SOURCE_INSTANCES)
    top5 = [h["params"] for h in sorted(source_history, key=lambda h: h["value"], reverse=True)[:5]]
    cold_curve, _ = run_engine("cold", rep, rep + 1000, TARGET_TRIALS, BATCH, TARGET_INSTANCES)
    warm_curve, _ = run_engine("warm", rep, rep + 1000, TARGET_TRIALS, BATCH, TARGET_INSTANCES, top5)
    return {
        "rep": rep,
        "source_best60": source_curve[-1],
        "top5": top5,
        "cold_best5": cold_curve[min(4, len(cold_curve) - 1)],
        "cold_best10": cold_curve[min(9, len(cold_curve) - 1)],
        "cold_best30": cold_curve[min(29, len(cold_curve) - 1)],
        "warm_best5": warm_curve[min(4, len(warm_curve) - 1)],
        "warm_best10": warm_curve[min(9, len(warm_curve) - 1)],
        "warm_best30": warm_curve[min(29, len(warm_curve) - 1)],
    }


def boot_ci(diffs, n=10000, seed=0):
    rng = random.Random(seed)
    ms = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(n))
    return ms[int(0.025 * n)], ms[int(0.975 * n)]


def summarize(rows: list, reps: int) -> dict:
    rows = sorted(rows, key=lambda r: r["rep"])
    summary = {"reps": reps, "source_trials": SOURCE_TRIALS, "target_trials": TARGET_TRIALS, "batch": BATCH}
    for k in (5, 10, 30):
        cold_key = f"cold_best{k}"
        warm_key = f"warm_best{k}"
        cold = [r[cold_key] for r in rows]
        warm = [r[warm_key] for r in rows]
        diffs = [b - a for a, b in zip(cold, warm)]
        lo, hi = boot_ci(diffs)
        entry = {
            "cold_mean": statistics.fmean(cold),
            "cold_sd": statistics.stdev(cold) if len(cold) > 1 else 0.0,
            "warm_mean": statistics.fmean(warm),
            "warm_sd": statistics.stdev(warm) if len(warm) > 1 else 0.0,
            "paired_diff_warm_minus_cold_mean": statistics.fmean(diffs),
            "paired_diff_ci95": [lo, hi],
            "wins": sum(x > 0 for x in diffs),
            "losses": sum(x < 0 for x in diffs),
            "reps": reps,
        }
        summary[f"best{k}"] = entry
        print(f"best{k}", json.dumps(entry, ensure_ascii=False))
    return summary


def main():
    parser = argparse.ArgumentParser(description="Validate warm-start from previous study.")
    parser.add_argument("--quick", action="store_true", help="縮小版(REPSとtrial数を約1/6)")
    args = parser.parse_args()

    global SOURCE_TRIALS, TARGET_TRIALS, REPS
    if args.quick:
        SOURCE_TRIALS = 10
        TARGET_TRIALS = 5
        REPS = 5

    with Pool(POOL_PROCESSES, initializer=init_worker, initargs=(SOURCE_TRIALS, TARGET_TRIALS)) as pool:
        rows = pool.map(run_rep, list(range(REPS)))
    out = {"rows": rows, "summary": summarize(rows, REPS)}
    (ROOT / "experiments" / "results_warmstart.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
