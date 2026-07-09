#!/usr/bin/env python3
"""検証E2: CMA-ES with Margin / lr_adapt vs TPE推奨設定.

mock_problem の焼きなましソルバー(5インスタンスseedの平均スコア)を目的関数に、
実運用のブリッジと同じ「バッチ8のask/tell」で60trial探索する。
各armを同一rep seedで対にし、best@20 / best@40 / best@60 を比較する。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import statistics
import sys
import tempfile
import time
import warnings
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
POOL_PROCESSES = 10


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


def suggest(trial) -> dict:
    return {
        "iters": trial.suggest_int("iters", 100, 600, log=True),
        "t0": trial.suggest_float("t0", 0.1, 50.0, log=True),
        "cooling": trial.suggest_float("cooling", 0.0001, 0.5, log=True),
    }


def run_tpe(rep: int) -> dict:
    import optuna
    import optuna_bridge as ob

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    tmp = Path(tempfile.mkdtemp())
    eng = ob.OptunaEngine(SPEC, "max", tmp / "j.log", f"tpe-{rep}", tpe_profile="recommended", seed=rep)
    curve = []
    best = None
    sampler_time = 0.0
    done = 0
    while done < TRIALS:
        n = min(BATCH, TRIALS - done)
        asked = []
        for _ in range(n):
            start = time.perf_counter()
            asked.append(eng.ask())
            sampler_time += time.perf_counter() - start
        for ref, params in asked:
            value = evaluate(params, SPEC["instances"])
            start = time.perf_counter()
            eng.tell(ref, params, value)
            sampler_time += time.perf_counter() - start
            best = value if best is None or value > best else best
            curve.append(best)
        done += n
    return row("tpe", rep, curve, sampler_time, 0, None)


def make_cmaes_sampler(optuna, arm: str, rep: int):
    if arm == "cmaes":
        return optuna.samplers.CmaEsSampler(with_margin=True, seed=rep), None
    try:
        return optuna.samplers.CmaEsSampler(lr_adapt=True, seed=rep), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def run_cmaes(arm: str, rep: int) -> dict:
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    sampler, skipped = make_cmaes_sampler(optuna, arm, rep)
    if skipped:
        return {"arm": arm, "rep": rep, "skipped": skipped}
    study = optuna.create_study(direction="maximize", sampler=sampler)
    curve = []
    best = None
    sampler_time = 0.0
    independent_fallback_warnings = 0
    done = 0
    while done < TRIALS:
        n = min(BATCH, TRIALS - done)
        asked = []
        for _ in range(n):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                start = time.perf_counter()
                trial = study.ask()
                params = suggest(trial)
                sampler_time += time.perf_counter() - start
            independent_fallback_warnings += count_independent_warnings(caught)
            asked.append((trial, params))
        for trial, params in asked:
            value = evaluate(params, SPEC["instances"])
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                start = time.perf_counter()
                study.tell(trial, value)
                sampler_time += time.perf_counter() - start
            independent_fallback_warnings += count_independent_warnings(caught)
            best = value if best is None or value > best else best
            curve.append(best)
        done += n
    return row(arm, rep, curve, sampler_time, independent_fallback_warnings, None)


def count_independent_warnings(caught) -> int:
    return sum(1 for w in caught if "independent sampl" in str(w.message).lower())


def row(arm: str, rep: int, curve: list, sampler_time: float, independent_warnings: int, skipped) -> dict:
    return {
        "arm": arm,
        "rep": rep,
        "best20": curve[min(19, len(curve) - 1)],
        "best40": curve[min(39, len(curve) - 1)],
        "best60": curve[min(59, len(curve) - 1)],
        "sampler_time_sec": sampler_time,
        "independent_fallback_warnings": independent_warnings,
        "skipped": skipped,
    }


def run_rep(job) -> dict:
    arm, rep = job
    if arm == "tpe":
        return run_tpe(rep)
    return run_cmaes(arm, rep)


def boot_ci(diffs, n=10000, seed=0):
    rng = random.Random(seed)
    ms = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(n))
    return ms[int(0.025 * n)], ms[int(0.975 * n)]


def summarize(rows: list, reps: int) -> dict:
    usable_arms = sorted({r["arm"] for r in rows if not r.get("skipped")})
    by = {arm: sorted([r for r in rows if r["arm"] == arm and not r.get("skipped")], key=lambda r: r["rep"])
          for arm in usable_arms}
    summary = {
        "reps": reps,
        "trials": TRIALS,
        "batch": BATCH,
        "skipped": [r for r in rows if r.get("skipped")],
        "sampler_time_sec_mean": {
            arm: statistics.fmean(r["sampler_time_sec"] for r in arm_rows) for arm, arm_rows in by.items()
        },
        "independent_fallback_warnings": {
            arm: sum(r["independent_fallback_warnings"] for r in arm_rows) for arm, arm_rows in by.items()
        },
    }
    tpe = by["tpe"]
    for k in ("best20", "best40", "best60"):
        summary[k] = {}
        for arm, arm_rows in by.items():
            vals = [r[k] for r in arm_rows]
            entry = {
                "mean": statistics.fmean(vals),
                "sd": statistics.stdev(vals) if len(vals) > 1 else 0.0,
                "reps": len(vals),
            }
            if arm != "tpe" and len(arm_rows) == len(tpe):
                diffs = [b[k] - a[k] for a, b in zip(tpe, arm_rows)]
                lo, hi = boot_ci(diffs)
                entry.update({
                    "paired_diff_vs_tpe_mean": statistics.fmean(diffs),
                    "paired_diff_vs_tpe_ci95": [lo, hi],
                    "wins": sum(x > 0 for x in diffs),
                    "losses": sum(x < 0 for x in diffs),
                })
            summary[k][arm] = entry
        print(k, json.dumps(summary[k], ensure_ascii=False))
    print("sampler_time_sec_mean", json.dumps(summary["sampler_time_sec_mean"], ensure_ascii=False))
    return summary


def main():
    parser = argparse.ArgumentParser(description="Validate CMA-ES samplers against recommended TPE.")
    parser.add_argument("--quick", action="store_true", help="縮小版(REPSとtrial数を約1/6)")
    args = parser.parse_args()

    global TRIALS, REPS
    if args.quick:
        TRIALS = 10
        REPS = 5

    jobs = [(arm, rep) for arm in ("tpe", "cmaes", "cmaes_lr") for rep in range(REPS)]
    with Pool(POOL_PROCESSES, initializer=init_worker, initargs=(TRIALS,)) as pool:
        rows = pool.map(run_rep, jobs)
    out = {"rows": rows, "summary": summarize(rows, REPS)}
    (ROOT / "experiments" / "results_cmaes.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
