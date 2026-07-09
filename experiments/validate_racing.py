#!/usr/bin/env python3
"""Wilcoxonゲート付きシードレーシング検証.

successive halving(シード数=忠実度)で観測された誤淘汰を避けるため、
「統計検定で明白に劣る」場合だけpartial trialを刈る保守的レーシングを
同一seed評価予算で検証する。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
import statistics
import sys
import tempfile
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

CONFIGS = {
    "n5_b400": {"instances": list(range(1, 6)), "budget": 400},
    "n20_b1600": {"instances": list(range(1, 21)), "budget": 1600},
}
ARMS = {
    "full": {"p_threshold": None, "n_startup": None},
    "race10": {"p_threshold": 0.10, "n_startup": 2},
    "race05": {"p_threshold": 0.05, "n_startup": "ceil_third"},
}
REPS = 30
POOL_PROCESSES = 10


def evaluate_seed(params, seed):
    sites = M.make_instance(seed)
    chosen = M.solve(sites, int(params["iters"]), float(params["t0"]), float(params["cooling"]),
                     random.Random(seed + 12345))
    return M.score_selection(sites, chosen)


def mean_score(scores):
    return statistics.fmean(scores.values())


def signed_rank_p_worse(diffs):
    """One-sided Pratt Wilcoxon signed-rank p-value for challenger < incumbent.

    diffs are challenger_score - incumbent_score. Small p means the challenger is
    significantly worse. This function intentionally uses only the standard
    library so it can be moved into scripts/optuna_bridge.py later.
    """
    vals = [float(d) for d in diffs if math.isfinite(float(d))]
    if not vals:
        return 1.0

    ordered = sorted(enumerate(vals), key=lambda item: abs(item[1]))
    ranks = [0.0] * len(vals)
    tie_groups = []
    i = 0
    while i < len(ordered):
        j = i + 1
        a = abs(ordered[i][1])
        while j < len(ordered) and abs(ordered[j][1]) == a:
            j += 1
        rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[ordered[k][0]] = rank
        tie_groups.append(j - i)
        i = j

    nonzero = [(d, ranks[idx]) for idx, d in enumerate(vals) if d != 0.0]
    m = len(nonzero)
    if m == 0:
        return 1.0

    w_plus = sum(rank for d, rank in nonzero if d > 0.0)
    if m <= 25:
        rank2 = [int(round(rank * 2.0)) for _, rank in nonzero]
        obs = int(round(w_plus * 2.0))
        counts = {0: 1}
        for r in rank2:
            nxt = {}
            for s, c in counts.items():
                nxt[s] = nxt.get(s, 0) + c
                sr = s + r
                nxt[sr] = nxt.get(sr, 0) + c
            counts = nxt
        le = sum(c for s, c in counts.items() if s <= obs)
        return le / float(2 ** m)

    var = m * (m + 1) * (2 * m + 1) / 24.0
    var -= sum((t ** 3 - t) / 48.0 for t in tie_groups)
    if var <= 0.0:
        return 1.0
    z = (w_plus - m * (m + 1) / 4.0 + 0.5) / math.sqrt(var)
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def selftest():
    assert abs(signed_rank_p_worse([-1, -2, -3, -4]) - 0.0625) < 1e-12
    assert signed_rank_p_worse([1, 2, 3, 4]) > 0.999
    assert abs(signed_rank_p_worse([0, -1, 2]) - 0.75) < 1e-12
    assert abs(signed_rank_p_worse([0, -1, -2]) - 0.25) < 1e-12
    assert signed_rank_p_worse([0, 0, 0]) == 1.0
    assert 0.0 <= signed_rank_p_worse([-1, -1, 2, 3, 0]) <= 1.0


def curve_at(curve, budget):
    val = None
    for spent, best in curve:
        if spent > budget:
            break
        val = best
    return val


def row_result(config_name, arm, rep, budget, curve, completed, pruned, false_prunes,
               saved_seed_evals, theoretical_full_seed_evals, consumed_seed_evals):
    best50 = curve_at(curve, budget * 0.5)
    best100 = curve_at(curve, budget)
    prune_rate = pruned / completed if completed else 0.0
    saved_ratio = saved_seed_evals / theoretical_full_seed_evals if theoretical_full_seed_evals else 0.0
    false_prune_rate = false_prunes / pruned if pruned else 0.0
    return {
        "config": config_name,
        "arm": arm,
        "rep": rep,
        "budget": budget,
        "consumed_seed_evals": consumed_seed_evals,
        "best50": best50,
        "best100": best100,
        "completed_trials": completed,
        "pruned_trials": pruned,
        "prune_rate": prune_rate,
        "saved_seed_evals": saved_seed_evals,
        "saved_seed_eval_ratio": saved_ratio,
        "false_prunes": false_prunes,
        "false_prune_rate": false_prune_rate,
        "curve": curve,
    }


def run_one(job):
    config_name, config, arm, rep = job
    import optuna
    import optuna_bridge as ob

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    warnings.filterwarnings("ignore", category=optuna.exceptions.ExperimentalWarning)
    instances = list(config["instances"])
    budget = int(config["budget"])
    spec = dict(SPEC, instances=instances)
    tmp = Path(tempfile.mkdtemp())
    eng = ob.OptunaEngine(spec, "max", tmp / "j.log", "%s-%s-%d" % (config_name, arm, rep),
                          tpe_profile="recommended", seed=rep)

    p_threshold = ARMS[arm]["p_threshold"]
    n_startup_cfg = ARMS[arm]["n_startup"]
    n_startup = int(math.ceil(len(instances) / 3.0)) if n_startup_cfg == "ceil_third" else n_startup_cfg

    incumbent = None
    curve = []
    consumed = 0
    completed = 0
    pruned = 0
    false_prunes = 0
    saved = 0
    theoretical = 0
    trial_no = 0

    while consumed < budget:
        ref, params = eng.ask()
        order = list(instances)
        random.Random(rep * 10000 + trial_no).shuffle(order)
        trial_no += 1

        scores = {}
        pruned_this = False
        for seed in order:
            if consumed >= budget:
                return row_result(config_name, arm, rep, budget, curve, completed, pruned, false_prunes,
                                  saved, theoretical, consumed)
            scores[seed] = evaluate_seed(params, seed)
            consumed += 1

            if (p_threshold is not None and incumbent is not None and len(scores) >= n_startup
                    and len(scores) < len(instances)):
                diffs = [scores[s] - incumbent["scores"][s] for s in scores]
                p = signed_rank_p_worse(diffs)
                partial_mean = mean_score(scores)
                if p < p_threshold and partial_mean < incumbent["mean"]:
                    eng.tell(ref, params, partial_mean)
                    remaining = [s for s in order if s not in scores]
                    full_scores = dict(scores)
                    for rest_seed in remaining:
                        full_scores[rest_seed] = evaluate_seed(params, rest_seed)
                    if mean_score(full_scores) > incumbent["mean"]:
                        false_prunes += 1
                    pruned += 1
                    completed += 1
                    saved += len(remaining)
                    theoretical += len(instances)
                    curve.append((consumed, incumbent["mean"]))
                    pruned_this = True
                    break

        if pruned_this:
            continue
        if len(scores) < len(instances):
            return row_result(config_name, arm, rep, budget, curve, completed, pruned, false_prunes,
                              saved, theoretical, consumed)

        value = mean_score(scores)
        eng.tell(ref, params, value)
        if incumbent is None or value > incumbent["mean"]:
            incumbent = {"mean": value, "scores": scores}
        completed += 1
        theoretical += len(instances)
        curve.append((consumed, incumbent["mean"]))

    return row_result(config_name, arm, rep, budget, curve, completed, pruned, false_prunes,
                      saved, theoretical, consumed)


def boot_ci(diffs, n=10000, seed=0):
    rng = random.Random(seed)
    ms = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(n))
    return ms[int(0.025 * n)], ms[int(0.975 * n)]


def summarize_metric(metric, arm_rows, full_rows):
    vals = [r[metric] for r in arm_rows if r[metric] is not None]
    entry = {
        "mean": statistics.fmean(vals) if vals else None,
        "sd": statistics.stdev(vals) if len(vals) > 1 else 0.0,
        "reps": len(vals),
    }
    if arm_rows and full_rows and arm_rows[0]["arm"] != "full" and len(arm_rows) == len(full_rows):
        diffs = [b[metric] - a[metric] for a, b in zip(full_rows, arm_rows)
                 if a[metric] is not None and b[metric] is not None]
        if diffs:
            lo, hi = boot_ci(diffs)
            entry.update({
                "paired_diff_vs_full_mean": statistics.fmean(diffs),
                "paired_diff_vs_full_ci95": [lo, hi],
                "wins": sum(x > 0 for x in diffs),
                "losses": sum(x < 0 for x in diffs),
            })
    return entry


def summarize(rows, reps, configs):
    summary = {"reps": reps, "configs": {}}
    metrics = (
        "best50", "best100", "completed_trials", "pruned_trials", "prune_rate",
        "saved_seed_eval_ratio", "false_prune_rate",
    )
    for config_name in sorted(configs):
        cfg_rows = [r for r in rows if r["config"] == config_name]
        by = {arm: sorted([r for r in cfg_rows if r["arm"] == arm], key=lambda r: r["rep"]) for arm in ARMS}
        cfg_summary = {}
        full_rows = by["full"]
        for arm in ARMS:
            arm_summary = {metric: summarize_metric(metric, by[arm], full_rows) for metric in metrics}
            cfg_summary[arm] = arm_summary
            line = {
                "best50": arm_summary["best50"]["mean"],
                "best100": arm_summary["best100"]["mean"],
                "diff100": arm_summary["best100"].get("paired_diff_vs_full_mean"),
                "trials": arm_summary["completed_trials"]["mean"],
                "pruned": arm_summary["pruned_trials"]["mean"],
                "saved_ratio": arm_summary["saved_seed_eval_ratio"]["mean"],
                "false_prune_rate": arm_summary["false_prune_rate"]["mean"],
            }
            print(config_name, arm, json.dumps(line, ensure_ascii=False))
        summary["configs"][config_name] = cfg_summary
    return summary


def main():
    parser = argparse.ArgumentParser(description="Validate Wilcoxon-gated seed racing.")
    parser.add_argument("--quick", action="store_true", help="縮小版(REPS=5, 予算を約1/6)")
    parser.add_argument("--selftest", action="store_true", help="Wilcoxon検定関数の自己検証だけを実行")
    args = parser.parse_args()

    if args.selftest:
        selftest()
        print("selftest ok")
        return

    global REPS
    configs = CONFIGS
    if args.quick:
        REPS = 5
        configs = {
            "n5_b70": {"instances": list(range(1, 6)), "budget": 70},
            "n20_b270": {"instances": list(range(1, 21)), "budget": 270},
        }

    jobs = [(config_name, config, arm, rep)
            for config_name, config in configs.items()
            for arm in ARMS
            for rep in range(REPS)]
    with Pool(POOL_PROCESSES) as pool:
        rows = pool.map(run_one, jobs)
    out = {"rows": rows, "summary": summarize(rows, REPS, configs)}
    (ROOT / "experiments" / "results_racing.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
