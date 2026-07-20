#!/usr/bin/env python3
"""Move failed jobs back to pending, and optionally recover stale running jobs."""

from __future__ import annotations

import _pyversion  # noqa: F401  Pythonバージョン検査(3.9未満なら即エラー)

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


# 注意: この存在チェックから書き込みまではアトミックではない（TOCTOU）。
# job_id の一意性は呼び出し側（make_job.py / optuna_bridge.py）の ID 採番が保証する前提で、
# 同一 job_id の 2 ジョブが同時に完了することは通常の運用では起こらない。
# 将来この前提を崩す変更を加える場合は、この関数の非アトミック性を再検討すること。
def unique_path(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for i in range(2, 10000):
        numbered = directory / f"{stem}-{i}{suffix}"
        if not numbered.exists():
            return numbered
    raise RuntimeError(f"could not allocate unique path for {candidate}")


def worker_status_is_stale(root: Path, worker_id: str, stale_sec: float) -> bool:
    status_path = root / "status" / f"{worker_id}.json"
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
        updated = datetime.fromisoformat(str(data.get("updated_at", ""))).astimezone()
    except Exception:
        return True
    age = (datetime.now(updated.tzinfo) - updated).total_seconds()
    return age > stale_sec


def requeue_stale_running(root: Path, pending: Path, stale_sec: float) -> int:
    running = root / "jobs" / "running"
    if not running.exists():
        return 0
    now = time.time()
    moved = 0
    for path in sorted(running.glob("*.json")):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if now - mtime <= stale_sec:
            continue
        job_id, _, worker_id = path.stem.partition("--")
        if worker_id and not worker_status_is_stale(root, worker_id, stale_sec):
            continue
        target = unique_path(pending, f"{job_id}.json")
        try:
            os.replace(path, target)
        except OSError:
            continue
        moved += 1
    return moved


def main() -> int:
    parser = argparse.ArgumentParser(description="Requeue failed SuperCon cluster jobs.")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--stale-running-sec",
        type=float,
        default=None,
        help="also requeue jobs stuck in running/ older than N seconds whose worker status is stale",
    )
    args = parser.parse_args()

    failed = args.root / "jobs" / "failed"
    pending = args.root / "jobs" / "pending"
    pending.mkdir(parents=True, exist_ok=True)

    moved = 0
    for path in sorted(failed.glob("*.json")):
        if args.limit is not None and moved >= args.limit:
            break
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
            job_id = str(job.get("job_id") or path.stem.split("--", 1)[0])
        except Exception:
            job_id = path.stem.split("--", 1)[0]
        target = unique_path(pending, f"{job_id}.json")
        os.replace(path, target)
        moved += 1

    print(f"requeued {moved} failed job(s)")

    if args.stale_running_sec is not None:
        recovered = requeue_stale_running(args.root, pending, args.stale_running_sec)
        print(f"recovered {recovered} stale running job(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
