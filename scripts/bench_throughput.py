#!/usr/bin/env python3
"""Measure cluster throughput (jobs/min) with dummy jobs. For slot-count tuning."""

from __future__ import annotations

import _pyversion  # noqa: F401  Pythonバージョン検査(3.9未満なら即エラー)

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def count_done(root: Path, prefix: str) -> int:
    return sum(1 for _ in (root / "jobs" / "done").glob(f"{prefix}*.json"))


def per_worker_counts(root: Path, prefix: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in (root / "results").glob(f"*/{prefix}*/result.json"):
        worker = path.parent.parent.name
        counts[worker] = counts.get(worker, 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark cluster throughput with dummy jobs.")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--minutes", type=float, default=10.0, help="measurement duration")
    parser.add_argument("--count", type=int, default=2000, help="jobs to enqueue")
    parser.add_argument("--seconds", type=float, default=30.0, help="per-job dummy duration")
    parser.add_argument("--report-sec", type=float, default=30.0, help="progress report interval")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    prefix = "bench" + datetime.now().strftime("%H%M%S") + "-"
    pending = root / "jobs" / "pending"
    pending.mkdir(parents=True, exist_ok=True)

    for i in range(1, args.count + 1):
        job_id = f"{prefix}{i:05d}"
        atomic_write_json(
            pending / f"{job_id}.json",
            {
                "job_id": job_id,
                "seed": i,
                "elapsed": args.seconds,
                "timeout_sec": args.seconds + 60,
                "command": "dummy",
                "created_at": now_iso(),
            },
        )
    print(f"enqueued {args.count} dummy jobs ({args.seconds}s each), prefix={prefix}")

    start = time.time()
    deadline = start + args.minutes * 60
    while time.time() < deadline:
        time.sleep(min(args.report_sec, max(1.0, deadline - time.time())))
        elapsed_min = (time.time() - start) / 60
        done = count_done(root, prefix)
        print(f"[{elapsed_min:5.1f} min] done={done} ({done / elapsed_min:.1f} jobs/min)")

    done = count_done(root, prefix)
    elapsed_min = (time.time() - start) / 60
    print()
    print(f"=== result: {done} jobs in {elapsed_min:.1f} min = {done / elapsed_min:.1f} jobs/min ===")

    counts = per_worker_counts(root, prefix)
    for worker in sorted(counts):
        print(f"  {worker:<16} {counts[worker]}")
    remaining = sum(1 for _ in pending.glob(f"{prefix}*.json"))
    if remaining:
        print(f"note: {remaining} bench jobs still pending; delete jobs\\pending\\{prefix}*.json to clean up")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
