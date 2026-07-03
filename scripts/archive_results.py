#!/usr/bin/env python3
"""Archive done jobs and results into a dated folder to keep active dirs small."""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def move_children(source: Path, target: Path) -> int:
    if not source.exists():
        return 0
    target.mkdir(parents=True, exist_ok=True)
    moved = 0
    for child in sorted(source.iterdir()):
        dest = target / child.name
        if dest.exists():
            dest = target / f"{child.name}-{moved}"
        os.replace(child, dest)
        moved += 1
    return moved


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive done/ and results/ into archive/<date>/.")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--label", default=None, help="archive folder name (default: timestamp)")
    args = parser.parse_args()

    label = args.label or datetime.now().strftime("%Y%m%d-%H%M%S")
    archive = args.root / "archive" / label

    done_moved = move_children(args.root / "jobs" / "done", archive / "done")
    failed_moved = move_children(args.root / "jobs" / "failed", archive / "failed")
    results_moved = move_children(args.root / "results", archive / "results")

    print(f"archived to {archive}: done={done_moved} failed={failed_moved} result-dirs={results_moved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
