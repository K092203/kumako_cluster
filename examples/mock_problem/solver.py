#!/usr/bin/env python3
"""模擬本選問題: 発電機配置の最適化(焼きなまし)。

seed からインスタンス(候補地50点・基礎価値)を決定的に生成し、
K=8 点を選んで「基礎価値の合計 - 近接ペナルティ」を最大化する。
2025年本選課題(洋上風力タービン配置)を模した練習用。

出力: --out に選択インデックス、stderr に #TUNE score=... 行。
"""

from __future__ import annotations

import argparse
import math
import random
import time
from pathlib import Path

N_SITES = 50
K_SELECT = 8
FIELD = 100.0
NEAR = 15.0
PENALTY = 0.5


def make_instance(seed: int) -> list[tuple[float, float, float]]:
    rng = random.Random(seed * 7919 + 1)
    return [
        (rng.uniform(0, FIELD), rng.uniform(0, FIELD), rng.uniform(1.0, 10.0))
        for _ in range(N_SITES)
    ]


def score_selection(sites: list[tuple[float, float, float]], chosen: list[int]) -> float:
    total = sum(sites[i][2] for i in chosen)
    for a in range(len(chosen)):
        for b in range(a + 1, len(chosen)):
            xa, ya, _ = sites[chosen[a]]
            xb, yb, _ = sites[chosen[b]]
            dist = math.hypot(xa - xb, ya - yb)
            if dist < NEAR:
                total -= (NEAR - dist) * PENALTY
    return total


def solve(sites, iters: int, t0: float, cooling: float, rng: random.Random) -> list[int]:
    chosen = rng.sample(range(N_SITES), K_SELECT)
    best = list(chosen)
    current_score = score_selection(sites, chosen)
    best_score = current_score
    for step in range(iters):
        t = t0 * (cooling ** (step / max(1, iters)))
        out_idx = rng.randrange(K_SELECT)
        candidates = [i for i in range(N_SITES) if i not in chosen]
        new_site = rng.choice(candidates)
        trial = list(chosen)
        trial[out_idx] = new_site
        trial_score = score_selection(sites, trial)
        if trial_score >= current_score or rng.random() < math.exp((trial_score - current_score) / max(t, 1e-9)):
            chosen = trial
            current_score = trial_score
            if current_score > best_score:
                best_score = current_score
                best = list(chosen)
    return best


def main() -> int:
    parser = argparse.ArgumentParser(description="Mock placement solver (simulated annealing).")
    parser.add_argument("--seed", type=int, required=True, help="instance seed")
    parser.add_argument("--iters", type=int, default=2000)
    parser.add_argument("--t0", type=float, default=5.0)
    parser.add_argument("--cooling", type=float, default=0.01)
    parser.add_argument("--sa-seed", type=int, default=0, help="annealing rng seed")
    parser.add_argument("--out", type=Path, default=Path("out/solution.txt"))
    args = parser.parse_args()

    start = time.perf_counter()
    sites = make_instance(args.seed)
    rng = random.Random(args.sa_seed if args.sa_seed else args.seed + 12345)
    chosen = solve(sites, args.iters, args.t0, args.cooling, rng)
    score = score_selection(sites, chosen)
    elapsed = time.perf_counter() - start

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(" ".join(map(str, sorted(chosen))) + "\n", encoding="utf-8")
    print(f"seed={args.seed} score={score:.6f} chosen={sorted(chosen)}")
    import sys

    print(f"#TUNE elapsed={elapsed:.3f} score={score:.6f} correct=1", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
