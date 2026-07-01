#!/usr/bin/env python3
"""Move failed jobs back to pending."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Requeue failed SuperCon cluster jobs.")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--limit", type=int, default=None)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
