#!/usr/bin/env python3
"""Print current worker status files."""

from __future__ import annotations

import _pyversion  # noqa: F401  Pythonバージョン検査(3.9未満なら即エラー)

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_time(text: str) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).astimezone()
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Show SuperCon worker status.")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--stale-sec", type=int, default=60)
    args = parser.parse_args()

    now = datetime.now(timezone.utc).astimezone()
    rows = []
    for path in sorted((args.root / "status").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        updated_at = str(data.get("updated_at", ""))
        updated = parse_time(updated_at)
        age_sec = int((now - updated).total_seconds()) if updated else None
        status = str(data.get("status", ""))
        if age_sec is not None and age_sec > args.stale_sec:
            status = "stale"
        rows.append(
            [
                str(data.get("worker", path.stem)),
                status,
                str(data.get("current_job") or "-"),
                updated_at,
                str(age_sec if age_sec is not None else "-"),
                str(data.get("message", "")),
            ]
        )

    if not rows:
        print("no worker status yet")
        return 0

    headers = ["worker", "status", "job", "updated_at", "age_sec", "message"]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))

    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[i]) for i, value in enumerate(row)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
