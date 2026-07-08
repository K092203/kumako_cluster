#!/usr/bin/env python3
"""検証2: 少数run比較での mean vs IQM vs IQM+ブートストラップCI の判定信頼性.

実ソルバー(mock_problem)を大量に走らせて2設定のスコアプールを作り、
「クラスタの実運用と同じ少数run標本」を繰り返し引いて、
どの判定ルールがどれだけ真の優劣を外すかを数える。
真値 = 大標本(インスタンス×60 sa-seed)での平均。
"""

from __future__ import annotations

import importlib.util
import json
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from summarize_results import iqm, stratified_bootstrap_ci  # noqa: E402

_s = importlib.util.spec_from_file_location("mock_solver", ROOT / "examples/mock_problem/solver.py")
M = importlib.util.module_from_spec(_s)
_s.loader.exec_module(M)

INSTANCES = [1, 2, 3, 4, 5]
N_SASEEDS = 60


def run_config(iters, t0, cooling):
    pool = {}
    for inst in INSTANCES:
        sites = M.make_instance(inst)
        pool[inst] = [
            M.score_selection(sites, M.solve(sites, iters, t0, cooling, random.Random(sa * 100003 + inst)))
            for sa in range(N_SASEEDS)
        ]
    return pool


def true_value(pool):
    return statistics.fmean(statistics.fmean(v) for v in pool.values())


def sample_runs(pool, per_inst, rng):
    """クラスタ実運用の形: インスタンスごとに per_inst 本のrunを引く。"""
    strata = [rng.sample(pool[i], per_inst) for i in INSTANCES]
    flat = [v for st in strata for v in st]
    return flat, strata


def compare_pair(a, b, truth_a_better, out, label):
    rng = random.Random(7)
    for per_inst, k_point, k_ci in ((1, 4000, 1000), (3, 4000, 1000)):
        mean_err = iqm_err = 0
        for _ in range(k_point):
            fa, _ = sample_runs(a, per_inst, rng)
            fb, _ = sample_runs(b, per_inst, rng)
            if (statistics.fmean(fa) > statistics.fmean(fb)) != truth_a_better:
                mean_err += 1
            if (iqm(fa) > iqm(fb)) != truth_a_better:
                iqm_err += 1
        declared = correct = 0
        for j in range(k_ci):
            fa, sa_ = sample_runs(a, per_inst, rng)
            fb, sb_ = sample_runs(b, per_inst, rng)
            lo_a, hi_a = stratified_bootstrap_ci(sa_, iqm, n_boot=300, seed=j)
            lo_b, hi_b = stratified_bootstrap_ci(sb_, iqm, n_boot=300, seed=j + 1)
            if hi_b < lo_a or hi_a < lo_b:  # CI非重複のときだけ判定を宣言
                declared += 1
                if ((iqm(fa) > iqm(fb)) == truth_a_better):
                    correct += 1
        key = f"{label} n={per_inst * len(INSTANCES)}"
        out[key] = {
            "mean_error_rate": mean_err / k_point,
            "iqm_error_rate": iqm_err / k_point,
            "ci_declare_rate": declared / k_ci,
            "ci_precision_when_declared": correct / declared if declared else None,
            "resamples_point": k_point, "resamples_ci": k_ci,
        }
        print(key, json.dumps(out[key]))


def main():
    # iters>=1500では目的関数が飽和しrunノイズが消えるため、非飽和帯で比較する
    a = run_config(400, 5.0, 0.01)
    cand = {it: run_config(it, 5.0, 0.01) for it in (120, 150, 200, 250, 300, 350)}
    run_sd = statistics.stdev([v for vs in a.values() for v in vs])
    ta = true_value(a)
    out = {"config_a": {"iters": 400}, "true_mean_a": ta, "run_sd": run_sd, "pairs": {}}
    # 小・中・大の真のギャップを持つ3ペアで判定ルールを試す
    for label, target in (("small", 0.15), ("mid", 0.5), ("large", 1.0)):
        b_iters = min(cand, key=lambda it: abs(abs(ta - true_value(cand[it])) - target * run_sd))
        b = cand[b_iters]
        tb = true_value(b)
        gap = abs(ta - tb) / run_sd
        out["pairs"][label] = {"iters_b": b_iters, "true_mean_b": tb, "gap_in_sd": gap}
        print(label, json.dumps(out["pairs"][label]))
        compare_pair(a, b, ta > tb, out, label)
    (ROOT / "experiments" / "results_iqm.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
