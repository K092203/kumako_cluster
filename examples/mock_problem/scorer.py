#!/usr/bin/env python3
"""模擬問題の公式スコアラ役: 解ファイルを検証して独立にスコアを再計算する。

本選では審査側の採点器に相当。提出前に必ずこれで検証する練習をする。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from solver import K_SELECT, N_SITES, make_instance, score_selection


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and score a mock solution file.")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--solution", type=Path, required=True)
    args = parser.parse_args()

    tokens = args.solution.read_text(encoding="utf-8").split()
    try:
        chosen = [int(t) for t in tokens]
    except ValueError:
        print("INVALID: non-integer token", file=sys.stderr)
        return 1
    if len(chosen) != K_SELECT or len(set(chosen)) != K_SELECT:
        print(f"INVALID: need {K_SELECT} distinct indices", file=sys.stderr)
        return 1
    if any(i < 0 or i >= N_SITES for i in chosen):
        print("INVALID: index out of range", file=sys.stderr)
        return 1

    score = score_selection(make_instance(args.seed), chosen)
    print(f"VALID seed={args.seed} score={score:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
