#!/usr/bin/env python3
"""CPU-heavy sample solver for cluster load testing."""

from __future__ import annotations

import argparse
import hashlib
import multiprocessing as mp
import os
import time


def burn(args: tuple[int, float]) -> int:
    seed, deadline = args
    payload = f"supercon-load-{seed}-{os.getpid()}".encode("utf-8")
    count = 0
    digest = payload
    while time.perf_counter() < deadline:
        digest = hashlib.sha256(digest + payload).digest()
        count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    start = time.perf_counter()
    deadline = start + max(0.0, args.seconds)
    workers = max(1, args.workers)

    with mp.Pool(processes=workers) as pool:
        counts = pool.map(burn, [(args.seed + i, deadline) for i in range(workers)])

    elapsed = time.perf_counter() - start
    score = float(sum(counts))
    print(f"stress solver finished workers={workers} iterations={int(score)}")
    print(f"#TUNE elapsed={elapsed:.6f} score={score:.6f} correct=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
