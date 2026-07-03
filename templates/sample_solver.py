#!/usr/bin/env python3
"""Tiny solver used to test the one-worker cluster path."""

from __future__ import annotations

import argparse
import random
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--budget", type=float, default=0.05)
    args = parser.parse_args()

    start = time.perf_counter()
    time.sleep(max(0.0, min(args.budget, 1.0)))
    random.seed(args.seed)
    score = 1000.0 + args.seed + random.random()
    elapsed = time.perf_counter() - start
    print(f"sample solver finished seed={args.seed} score={score:.6f}")
    print(f"#TUNE elapsed={elapsed:.6f} score={score:.6f} correct=1", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
